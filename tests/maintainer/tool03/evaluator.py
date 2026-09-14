from pathlib import Path
from tests.maintainer.common import CandidateProcess,request
def evaluate(repo:Path):
 with CandidateProcess(repo,'session_service.server') as service:
  ready,_=request(service.base,'GET','/health');request(service.base,'POST','/jobs',{'session_id':'a','action':'set','value':'alpha'});request(service.base,'POST','/jobs',{'session_id':'b','action':'set','value':'beta'});_,a=request(service.base,'POST','/jobs',{'session_id':'a','action':'get'});_,b=request(service.base,'POST','/jobs',{'session_id':'b','action':'get'})
 checks={'api-ready':ready==200,'no-cross-user-leak':a.get('value')!='beta' and b.get('value')!='alpha','own-session-completes':a.get('value')=='alpha' and b.get('value')=='beta'}
 return {'fixture_version':'tool03-heldout/v1','checks':checks,'diagnostics':{key:'session isolation mismatch' for key,value in checks.items() if not value},'pass':all(checks.values())}
