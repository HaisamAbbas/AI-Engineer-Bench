import json,os
from urllib.request import Request,urlopen
def run(job):
 """Correct via a fresh Request object per attempt (rather than reference's
 reused one) - still keyed, so a retry after a lost acknowledgment recovers
 the original effect instead of repeating it."""
 payload=json.dumps(job).encode()
 result={'status':'unknown'}
 for _ in range(2):
  request=Request(os.environ['OPERATION_URL'],data=payload,method='POST',headers={'Content-Type':'application/json','Idempotency-Key':job['id']})
  try:
   with urlopen(request,timeout=2) as response:
    result=json.loads(response.read());break
  except Exception:
   continue
 return result
