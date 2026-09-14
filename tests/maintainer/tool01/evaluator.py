from __future__ import annotations
import json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from tests.maintainer.common import CandidateProcess,request
class Handler(BaseHTTPRequestHandler):
 events:list[dict]=[]
 def log_message(self,*a):pass
 def do_POST(self):
  n=int(self.headers.get("Content-Length","0"));job=json.loads(self.rfile.read(n));ok=job.get("id")!="fail";self.events.append({"id":job.get("id"),"ok":ok});data=json.dumps({"ok":ok}).encode();self.send_response(200);self.end_headers();self.wfile.write(data)
class Ledger:
 def __enter__(self):
  Handler.events=[];self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url=f"http://127.0.0.1:{self.server.server_port}/operate";return self
 def __exit__(self,*a):self.server.shutdown();self.thread.join();self.server.server_close()
def evaluate(repo:Path)->dict[str,object]:
 with Ledger() as ledger, CandidateProcess(repo,"workflow_service.server",{"OPERATION_URL":ledger.url}) as service:
  ready,_=request(service.base,"GET","/health"); _,failed=request(service.base,"POST","/jobs",{"id":"fail","payload":{"value":1}});_,success=request(service.base,"POST","/jobs",{"id":"ok","payload":{"value":2}})
 events=Handler.events
 checks={"api-ready":ready==200,"failed-operation-not-complete":failed.get("status")=="failed","genuine-success-completes":success.get("status")=="completed","external-ledger-authoritative":events==[{"id":"fail","ok":False},{"id":"ok","ok":True}]}
 return {"fixture_version":"tool01-heldout/v1","checks":checks,"diagnostics":{k:"HTTP response disagrees with external operation ledger" for k,v in checks.items() if not v},"pass":all(checks.values())}
