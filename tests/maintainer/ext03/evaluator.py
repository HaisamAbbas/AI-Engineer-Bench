from decimal import Decimal
from pathlib import Path
from tests.maintainer.common import CandidateProcess,request
def close(value,target):
 try:return abs(Decimal(str(value))-Decimal(target))<=Decimal('0.0001')
 except Exception:return False
def evaluate(repo:Path):
 with CandidateProcess(repo,'unit_service.server') as service:
  ready,_=request(service.base,'GET','/health');_,kg=request(service.base,'POST','/normalize',{'id':'kg','value':'1.25','unit':'kg','label':'keep'});_,mg=request(service.base,'POST','/normalize',{'id':'mg','value':'25','unit':'mg','label':'keep2'})
 checks={'api-ready':ready==200,'declared-conversions':close(kg.get('grams'), '1250') and close(mg.get('grams'),'0.025'),'decimal-tolerance':close(mg.get('grams'),'0.025'),'evidence-preserved':kg.get('evidence')=='1.25' and kg.get('unit')=='kg' and kg.get('label')=='keep'}
 return {'fixture_version':'ext03-heldout/v1','checks':checks,'diagnostics':{key:'unit contract mismatch' for key,value in checks.items() if not value},'pass':all(checks.values())}
