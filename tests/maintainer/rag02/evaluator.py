from pathlib import Path
from tests.maintainer.common import CandidateProcess,request
def evaluate(repo:Path):
 with CandidateProcess(repo,"search_service.server") as s:
  ready,_=request(s.base,"GET","/health");_,v=request(s.base,"POST","/search",{"query":"common","top_k":1,"metadata":{"team":"blue"}})
 hits=v.get("hits",[]);checks={"api-ready":ready==200,"filter-before-top-k":len(hits)==1 and hits[0].get("id")=="c","no-cross-filter-results":all(x.get("team")=="blue" for x in hits),"top-k-respected":len(hits)<=1}
 return {"checks":checks,"pass":all(checks.values()),"fixture_version":"rag02-heldout/v1","diagnostics":{k:"HTTP contract mismatch" for k,x in checks.items() if not x}}
