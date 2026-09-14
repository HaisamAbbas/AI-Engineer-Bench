from urllib.request import Request,urlopen
import json,os
def run(job):
 try:
  r=Request(os.environ["OPERATION_URL"],data=json.dumps(job).encode(),method="POST",headers={"Content-Type":"application/json"})
  with urlopen(r,timeout=3) as response: return {"id":job["id"],"status":"completed" if json.loads(response.read()).get("ok") else "failed"}
 except Exception:return {"id":job["id"],"status":"failed"}
