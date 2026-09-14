import json,os
from urllib.request import Request,urlopen
def run(job):
 payload=json.dumps(job).encode()
 result={'status':'unknown'}
 for _ in range(2):
  request=Request(os.environ['OPERATION_URL'],data=payload,method='POST',headers={'Content-Type':'application/json','Idempotency-Key':job['id']})
  with urlopen(request,timeout=2) as response:result=json.loads(response.read())
 return result
