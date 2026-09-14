import json,os
from urllib.request import Request,urlopen
def run(job):
 """Broken: retry after an ambiguous response repeats the write without a request key."""
 request=Request(os.environ['OPERATION_URL'],data=json.dumps(job).encode(),method='POST',headers={'Content-Type':'application/json'})
 result={'status':'unknown'}
 for _ in range(2):
  with urlopen(request,timeout=2) as response:result=json.loads(response.read())
 return result
