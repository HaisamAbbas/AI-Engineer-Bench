import json,os
from urllib.request import Request,urlopen
def run(job):
 """Broken: makes one keyed request and gives up on any failure instead of
 retrying - a lost acknowledgment is never resolved, so the caller is left
 with a genuinely unknown outcome even though the write already committed."""
 try:
  request=Request(os.environ['OPERATION_URL'],data=json.dumps(job).encode(),method='POST',headers={'Content-Type':'application/json','Idempotency-Key':job['id']})
  with urlopen(request,timeout=2) as response:return json.loads(response.read())
 except Exception:
  return {'status':'unknown'}
