from pathlib import Path
from tests.maintainer.common import CandidateProcess,request
def evaluate(repo:Path):
 with CandidateProcess(repo,'missingness_service.server') as service:
  ready,_=request(service.base,'GET','/health');_,missing=request(service.base,'POST','/extract',{'id':'missing'});_,present=request(service.base,'POST','/extract',{'id':'present','amount':0,'currency':'','note':None})
 checks={'api-ready':ready==200,'absent-not-invented':set(missing)=={'id'},'present-values-preserved':present=={'id':'present','amount':0,'currency':'','note':None}}
 return {'fixture_version':'ext01-heldout/v1','checks':checks,'diagnostics':{key:'missingness contract mismatch' for key,value in checks.items() if not value},'pass':all(checks.values())}
