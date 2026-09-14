from pathlib import Path
from tests.maintainer.common import CandidateProcess,request
def evaluate(repo:Path):
 items=[{'id':'first','text':'alpha'},{'id':'bad','text':None},{'id':'last','text':'omega'}]
 with CandidateProcess(repo,'batch_service.server') as service:
  ready,_=request(service.base,'GET','/health');status,response=request(service.base,'POST','/extract',{'items':items})
 values={item.get('id'):item.get('value') for item in response.get('results',[]) if isinstance(item,dict)};failures={item.get('id') for item in response.get('failures',[]) if isinstance(item,dict)}
 checks={'api-ready':ready==200 and status==200,'malformed-item-isolated':failures=={'bad'},'valid-results-retained':values=={'first':'ALPHA','last':'OMEGA'}}
 return {'fixture_version':'ext04-heldout/v1','checks':checks,'diagnostics':{key:'partial batch contract mismatch' for key,value in checks.items() if not value},'pass':all(checks.values())}
