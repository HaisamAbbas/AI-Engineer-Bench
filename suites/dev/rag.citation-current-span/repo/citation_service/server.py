import argparse,json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from .backend import update,search
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ready"}')
 def do_POST(self):
  n=int(self.headers.get('Content-Length','0'));v=json.loads(self.rfile.read(n));r={'ok':True} if self.path=='/update' and not update(v) else {'hits':search(v['query'])};d=json.dumps(r).encode();self.send_response(200);self.end_headers();self.wfile.write(d)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--port',type=int,required=True);a=p.parse_args();ThreadingHTTPServer(('127.0.0.1',a.port),H).serve_forever()
