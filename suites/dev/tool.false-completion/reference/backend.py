from urllib.request import Request,urlopen
import json,os
def run(job):
 r=Request(os.environ["OPERATION_URL"],data=json.dumps(job).encode(),method="POST",headers={"Content-Type":"application/json"})
 with urlopen(r,timeout=3) as response: outcome=json.loads(response.read())
 return {"id":job["id"],"status":"completed" if outcome.get("ok") else "failed"}
