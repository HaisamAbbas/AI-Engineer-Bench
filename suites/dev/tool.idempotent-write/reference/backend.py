import json,os
from urllib.request import Request,urlopen
def run(job):
 data=json.dumps(job).encode();headers={'Content-Type':'application/json','Idempotency-Key':job['id']}
 result={'status':'unknown'}
 for _ in range(2):
  with urlopen(Request(os.environ['OPERATION_URL'],data=data,method='POST',headers=headers),timeout=2) as response:result=json.loads(response.read())
 return result
