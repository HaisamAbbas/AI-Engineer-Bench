from urllib.request import Request,urlopen
import json,os
def run(job):
 request=Request(os.environ["OPERATION_URL"],data=json.dumps(job).encode(),method="POST",headers={"Content-Type":"application/json"})
 with urlopen(request,timeout=3) as response: response.read()
 return {"id":job["id"],"status":"completed"} # broken: ignores authoritative status
