import json,os
from urllib.request import Request,urlopen
def run(job):
 """Broken: retries after a lost acknowledgment, but never sends a request
 key, so the server cannot tell the retry apart from a brand-new write -
 the retry repeats the effect instead of recovering the original one."""
 data=json.dumps(job).encode()
 result={'status':'unknown'}
 for _ in range(2):
  try:
   with urlopen(Request(os.environ['OPERATION_URL'],data=data,method='POST',headers={'Content-Type':'application/json'}),timeout=2) as response:
    result=json.loads(response.read());break
  except Exception:
   continue
 return result
