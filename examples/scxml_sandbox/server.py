from scxml.pyscxml_server import PySCXMLServer, TYPE_WEBSOCKET, TYPE_DEFAULT, TYPE_RESPONSE, ioprocessor
import logging
import os, json
logging.basicConfig(level=logging.NOTSET)


json.dump(["example_docs/" + x for x in os.listdir("example_docs") if x.endswith("xml")], open("example_list.json", "w"))

server = PySCXMLServer("localhost", 8081, 
                        init_sessions={"server" : open("sandbox_server.xml").read()},
                        server_type=TYPE_RESPONSE | TYPE_WEBSOCKET
                        )


server.serve_forever()

