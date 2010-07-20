''' 
This file is part of pyscxml.

    pyscxml is free software: you can redistribute it and/or modify
    it under the terms of the GNU Lesser General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    pyscxml is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Lesser General Public License for more details.

    You should have received a copy of the GNU Lesser General Public License
    along with pyscxml.  If not, see <http://www.gnu.org/licenses/>.
    
    This is an implementation of the interpreter algorithm described in the W3C standard document, 
    which can be found at:
    
    http://www.w3.org/TR/2009/WD-scxml-20091029/ 
    
    @author Johan Roxendal
    @contact: johan@roxendal.com
'''


from node import *
import sys
import Queue
import threading
import time
from datastructures import OrderedSet
import scxml.pyscxml



class Interpreter(object):
    def __init__(self):
        self.g_continue = True
        self.configuration = OrderedSet()
        self.previousConfiguration = OrderedSet()
        
        self.internalQueue = Queue.Queue()
        self.externalQueue = Queue.Queue()
        
        self.statesToInvoke = OrderedSet()
        self.historyValue = {}
        self.dm = None
        self.invId = None
        
        self.timerDict = {}
        
    
    def interpret(self, document, optionalParentExternalQueue=None, invokeId=None):
        '''Initializes the interpreter given an SCXMLDocument instance'''
        
        self.doc = document
        self.dm = self.doc.datamodel
        self.dm["_parent"] = optionalParentExternalQueue
        self.invId = invokeId
        
        transition = Transition(document.rootState)
        transition.target = document.rootState.initial
        
        self.executeTransitionContent([transition])
        self.enterStates([transition])
        self.startEventLoop()
        
        
    
    def startEventLoop(self):
    
        initialStepComplete = False;
        while not initialStepComplete:
            enabledTransitions = self.selectEventlessTransitions()
            if enabledTransitions.isEmpty():
                if self.internalQueue.empty(): 
                    initialStepComplete = True 
                else:
                    internalEvent = self.internalQueue.get()
                    self.dm["_event"] = internalEvent
                    enabledTransitions = self.selectTransitions(internalEvent)
            if enabledTransitions:
                self.microstep(list(enabledTransitions))
        threading.Thread(target=self.mainEventLoop).start()
    
    
    
    def mainEventLoop(self):
        while self.g_continue:
            
            for state in self.statesToInvoke:
                for inv in state.invoke:
                    self.invoke(inv, self.externalQueue)
            self.statesToInvoke.clear()
            
            self.previousConfiguration = self.configuration
            
            externalEvent = self.externalQueue.get() # this call blocks until an event is available
            
            print "external event found: " + str(externalEvent.name)
            
            self.dm["_event"] = externalEvent
            if hasattr(externalEvent, "invokeid"):
                for state in self.configuration:
                    for inv in state.invoke:
                        if inv.invokeid == externalEvent.invokeid:  # event is the result of an <invoke> in this state
                            applyFinalize(inv, externalEvent)
            
            enabledTransitions = self.selectTransitions(externalEvent)
            if enabledTransitions:
                self.microstep(list(enabledTransitions))
                
                # now take any newly enabled null transitions and any transitions triggered by internal events
                macroStepComplete = False;
                while not macroStepComplete:
                    enabledTransitions = self.selectEventlessTransitions()
                    if enabledTransitions.isEmpty():
                        if self.internalQueue.empty(): 
                            macroStepComplete = True
                        else:
                            internalEvent = self.internalQueue.get() # this call returns immediately if no event is available
                            self.dm["event"] = internalEvent
                            enabledTransitions = self.selectTransitions(internalEvent)
    
                    if enabledTransitions:
                        self.microstep(list(enabledTransitions))
              
        # if we get here, we have reached a top-level final state or some external entity has set g_continue to False        
        self.exitInterpreter()  
         
    
    def exitInterpreter(self):
        inFinalState = False
        statesToExit = list(self.configuration)
        statesToExit.sort(exitOrder)
        for s in statesToExit:
            for content in s.onexit:
                self.executeContent(content)
            for inv in s.invoke:
                self.cancelInvoke(inv)
            if isFinalState(s) and isScxmlState(s.parent):
                inFinalState = True
            self.configuration.delete(s)
        if inFinalState:
            if self.invId and self.dm["_parent"]:
                self.dm["_parent"].put(Event(["done", "invoke", self.invId], {}))
            print "Exiting interpreter"
    #        sendDoneEvent(???)
    
    def selectEventlessTransitions(self):
        enabledTransitions = OrderedSet()
        atomicStates = filter(isAtomicState, self.configuration)
        for state in atomicStates:
            if not self.isPreempted(state, enabledTransitions):
                done = False
                for s in [state] + getProperAncestors(state, None):
                    if done: break
                    for t in s.transition:
                        if not t.event and self.conditionMatch(t): 
                            enabledTransitions.add(t)
                            done = True
                            break
        return enabledTransitions
    
    
    def selectTransitions(self, event):
        enabledTransitions = OrderedSet()
        atomicStates = filter(isAtomicState, self.configuration)
        for state in atomicStates:
            if hasattr(event, "invokeid") and state.invokeid == event.invokeid:  # event is the result of an <invoke> in this state
                applyFinalize(state, event)
                
            if not self.isPreempted(state, enabledTransitions):
                done = False
                for s in [state] + getProperAncestors(state, None):
                    if done: break
                    for t in s.transition:
                        if t.event and nameMatch(t.event, event.name) and self.conditionMatch(t):
                            enabledTransitions.add(t)
                            done = True
                            break 
        return enabledTransitions
    
    
    def isPreempted(self, s, transitionList):
        preempted = False
        for t in transitionList:
            if t.target:
                LCA = self.findLCA([t.source] + self.getTargetStates(t.target))
                if isDescendant(s,LCA):
                    preempted = True
                    break
        return preempted
    
    def microstep(self, enabledTransitions):
        self.exitStates(enabledTransitions)
        self.executeTransitionContent(enabledTransitions)
        self.enterStates(enabledTransitions)
        print "{" + ", ".join([s.id for s in self.configuration if s.id != "__main__"]) + "}"
    
    
    def exitStates(self, enabledTransitions):
        statesToExit = OrderedSet()
        for t in enabledTransitions:
            if t.target:
                LCA = self.findLCA([t.source] + self.getTargetStates(t.target))
                for s in self.configuration:
                    if isDescendant(s,LCA):
                        statesToExit.add(s)
        
        for s in statesToExit:
            self.statesToInvoke.delete(s)
        
        statesToExit = list(statesToExit)
        statesToExit.sort(exitOrder)
        
        for s in statesToExit:
            for h in s.history:
                if h.type == "deep":
                    f = lambda s0: isAtomicState(s0) and isDescendant(s0,s) 
                else:
                    f = lambda s0: s0.parent == s
                self.historyValue[h.id] = filter(f,self.configuration)
        for s in statesToExit:
            for content in s.onexit:
                self.executeContent(content)
            for inv in s.invoke:
                self.cancelInvoke(inv)
            self.configuration.delete(s)
    
    def invoke(self, inv, extQ):
        sm = scxml.pyscxml.StateMachine(inv.content)
        sm.start(extQ, inv.id)
        
        
    def cancelInvoke(self, inv):
        print "Cancelling: " + str(inv)
    
    def executeTransitionContent(self, enabledTransitions):
        for t in enabledTransitions:
            self.executeContent(t)
    
    
    def enterStates(self, enabledTransitions):
        statesToEnter = OrderedSet()
        statesForDefaultEntry = OrderedSet()
        for t in enabledTransitions:
            if t.target:
                LCA = self.findLCA([t.source] + self.getTargetStates(t.target))
                if isParallelState(LCA):
                    for child in getChildStates(LCA):
                        self.addStatesToEnter(child,LCA,statesToEnter,statesForDefaultEntry)
                for s in self.getTargetStates(t.target):
                    self.addStatesToEnter(s,LCA,statesToEnter,statesForDefaultEntry)
                    
        for s in statesToEnter:
            self.statesToInvoke.add(s)
                    
        statesToEnter = list(statesToEnter)
        statesToEnter.sort(enterOrder)
        for s in statesToEnter:
            self.configuration.add(s)
            for content in s.onentry:
                self.executeContent(content)
                
            if s in statesForDefaultEntry:
                # this can't be right, s.initial is a list
                self.executeContent(s.initial)
            if isFinalState(s):
                parent = s.parent
                grandparent = parent.parent
                self.internalQueue.put(Event(["done", "state", parent.id], {}))
                if isParallelState(grandparent):
                    if all(map(self.isInFinalState, getChildStates(grandparent))):
                        self.internalQueue.put(Event(["done", "state", grandparent.id], {}))
        for s in self.configuration:
            if isFinalState(s) and isScxmlState(s.parent):
                self.g_continue = False;
    
    
    def addStatesToEnter(self, s,root,statesToEnter,statesForDefaultEntry):
        
        if isHistoryState(s):
            if self.historyValue[s.id]:
                for s0 in self.historyValue[s.id]:
                    self.addStatesToEnter(s0, s, statesToEnter, statesForDefaultEntry)
            else:
                for t in s.transition:
                    for s0 in self.getTargetStates(t.target):
                        self.addStatesToEnter(s0, s, statesToEnter, statesForDefaultEntry)
        else:
            statesToEnter.add(s)
            if isParallelState(s):
                for child in getChildStates(s):
                    self.addStatesToEnter(child,s,statesToEnter,statesForDefaultEntry)
            elif isCompoundState(s):
                statesForDefaultEntry.add(s)
                for tState in self.getTargetStates(s.initial):
                    self.addStatesToEnter(tState, s, statesToEnter, statesForDefaultEntry)
            for anc in getProperAncestors(s,root):
                statesToEnter.add(anc)
                if isParallelState(anc):
                    for pChild in getChildStates(anc):
                        if not any(map(lambda s2: isDescendant(s2,pChild), statesToEnter)):
                            self.addStatesToEnter(pChild,anc,statesToEnter,statesForDefaultEntry)
    
    
    def isInFinalState(self, s):
        if isCompoundState(s):
            return any(map(lambda s: isFinalState(s) and s in self.configuration, getChildStates(s)))
        elif isParallelState(s):
            return all(map(self.isInFinalState, getChildStates(s)))
        else:
            return False
    
    def findLCA(self, stateList):
        for anc in getProperAncestors(stateList[0], None):
            print map(lambda(s): isDescendant(s,anc), stateList[1:])
            if all(map(lambda(s): isDescendant(s,anc), stateList[1:])):
                return anc
                
    
    def getTargetStates(self, targetIds):
        states = []
        for id in targetIds:
            states.append(self.doc.getState(id))
        return states
    
    def executeContent(self, obj):
        if hasattr(obj, "exe") and callable(obj.exe):
            obj.exe()
    
    def conditionMatch(self, t):
        In = self.In
        if not t.cond:
            return True
        else:
            return t.cond()
                
    def In(self, name):
        return name in map(lambda x: x.id, self.configuration)
    
    
    def send(self, name, sendid="", data={},delay=0):
        """Spawns a new thread that sends an event after a specified time, in seconds"""
        if type(name) == str: name = name.split(".")
        
        if delay == 0: 
            self.sendFunction(name, data)
            return
        timer = threading.Timer(delay, self.sendFunction, args=(name, data))
        if sendid:
            self.timerDict[sendid] = timer
        timer.start()
        
    def sendFunction(self, name, data):
        self.externalQueue.put(Event(name, data))
    
    def cancel(self, sendid):
        if self.timerDict.has_key(sendid):
            self.timerDict[sendid].cancel()
            del self.timerDict[sendid]
            
    def raiseFunction(self, event):
        self.internalQueue.put(Event(event, {}))


def getProperAncestors(state,root):
        ancestors = []
        while hasattr(state,'parent') and state.parent and state.parent != root:
            state = state.parent
            ancestors.append(state)
        
        return ancestors
    
    
def isDescendant(state1,state2):
    while hasattr(state1,'parent'):
        state1 = state1.parent
        if state1 == state2:
            return True
    return False


def getChildStates(state):
    return state.state + state.parallel + state.final + state.history


def nameMatch(eventList, event):
    if ["*"] in eventList: return True 
    def prefixList(l1, l2):
        if len(l1) > len(l2): return False 
        for tup in zip(l1, l2):
            if tup[0] != tup[1]:
                return False 
        return True 
    
    for elem in eventList:
        if prefixList(elem, event):
            return True 
    return False 

##
## Various tests for states
##

def isParallelState(s):
    return isinstance(s,Parallel)


def isFinalState(s):
    return isinstance(s,Final)


def isHistoryState(s):
    return isinstance(s,History)


def isScxmlState(s):
    return s.parent == None


def isAtomicState(s):
    return isinstance(s, Final) or (isinstance(s,SCXMLNode) and s.state == [] and s.parallel == [] and s.final == [])


def isCompoundState(s):
    return isinstance(s,SCXMLNode) and (s.state != [] or s.parallel != [] or s.final != [])


##
## Sorting orders
##

def documentOrder(s1,s2):
    if s1.n - s2.n:
        return 1
    else:
        return -1


def enterOrder(s1,s2):
    if isDescendant(s1,s2):
        return 1
    elif isDescendant(s2,s1):
        return -1
    else:
        return documentOrder(s1,s2)


def exitOrder(s1,s2):
    if isDescendant(s1,s2):
        return -1
    elif isDescendant(s2,s1):
        return 1
    else:
        return documentOrder(s2,s1)

class Event(object):
    def __init__(self, name, data):
        self.name = name
        self.data = data
    
    
