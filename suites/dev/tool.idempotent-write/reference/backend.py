import json,os
from urllib.request import Request,urlopen
def run(job):
 """Correct: retries a lost acknowledgment with the same request key, so a
 retry that lands after the original write already committed resolves to
 the original effect instead of repeating it."""
 data=json.dumps(job).encode();headers={'Content-Type':'application/json','Idempotency-Key':job['id']}
 result={'status':'unknown'}
 for _ in range(2):
  try:
   with urlopen(Request(os.environ['OPERATION_URL'],data=data,method='POST',headers=headers),timeout=2) as response:
    result=json.loads(response.read());break
  except Exception:
   continue
 return result
