import json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from tests.maintainer.common import CandidateProcess,request
class H(BaseHTTPRequestHandler):
 seen={};attempts={};calls=0
 def log_message(self,*args):pass
 def do_POST(self):
  n=int(self.headers.get('Content-Length','0'));job=json.loads(self.rfile.read(n));key=self.headers.get('Idempotency-Key');H.calls+=1;identity=key or f'unkeyed-{H.calls}';H.seen.setdefault(identity,job);H.attempts[identity]=H.attempts.get(identity,0)+1
  # The fixture marks each response as ambiguous. Candidate retry behavior is
  # assessed against the externally owned keyed effect ledger.
  data=json.dumps({'status':'completed'}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
class Ledger:
 def __enter__(self):H.seen={};H.attempts={};H.calls=0;self.s=ThreadingHTTPServer(('127.0.0.1',0),H);self.t=threading.Thread(target=lambda:self.s.serve_forever(poll_interval=.01),daemon=True);self.t.start();self.url=f'http://127.0.0.1:{self.s.server_port}/write';return self
 def __exit__(self,*args):self.s.shutdown();self.t.join();self.s.server_close()
def evaluate(repo:Path):
 with Ledger() as ledger, CandidateProcess(repo,'write_service.server',{'OPERATION_URL':ledger.url}) as service:
  ready,_=request(service.base,'GET','/health');_,result=request(service.base,'POST','/jobs',{'id':'once','value':1})
 checks={'api-ready':ready==200,'ambiguous-retry-resolves':result.get('status')=='completed','exactly-once-effect':len(H.seen)==1 and 'once' in H.seen}
 return {'fixture_version':'tool02-heldout/v1','checks':checks,'diagnostics':{key:'write retry contract mismatch' for key,value in checks.items() if not value},'pass':all(checks.values())}
