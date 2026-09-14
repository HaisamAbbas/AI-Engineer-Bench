from __future__ import annotations
import argparse,json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from .backend import process
class H(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def do_GET(self): self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ready"}')
 def do_POST(self):
  try:
   size=int(self.headers.get("Content-Length","0")); value=json.loads(self.rfile.read(size)); result=process(value["documents"]); data=json.dumps(result).encode();self.send_response(200);self.end_headers();self.wfile.write(data)
  except Exception as e: self.send_error(400,str(e))
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--port",type=int,required=True);a=p.parse_args();ThreadingHTTPServer(("127.0.0.1",a.port),H).serve_forever()
