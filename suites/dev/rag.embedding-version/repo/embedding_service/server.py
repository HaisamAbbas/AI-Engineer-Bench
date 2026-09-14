import argparse,json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from .backend import search
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ready"}')
 def do_POST(self):
  try:
   n=int(self.headers.get('Content-Length','0'));v=json.loads(self.rfile.read(n));d=json.dumps({'hits':search(v['query'],v['embedding_version'])}).encode();self.send_response(200);self.end_headers();self.wfile.write(d)
  except ValueError as e:self.send_error(409,str(e))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--port',type=int,required=True);a=p.parse_args();ThreadingHTTPServer(('127.0.0.1',a.port),H).serve_forever()
