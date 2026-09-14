from __future__ import annotations
from pathlib import Path
from tests.maintainer.common import CandidateProcess, request

def evaluate(repo: Path) -> dict[str, object]:
    docs=[{"id":"a","text":"alpha"},{"id":"b","text":"bravo","fail":True},{"id":"c","text":"charlie"},{"id":"a","text":"omega"}]
    with CandidateProcess(repo,"extraction_service.server") as service:
        ready,_=request(service.base,"GET","/health")
        status, response=request(service.base,"POST","/extract",{"documents":docs})
    results={item.get("id"):item.get("value") for item in response.get("results",[]) if isinstance(item,dict)}
    failures={item.get("id") for item in response.get("failures",[]) if isinstance(item,dict)}
    checks={"api-ready":ready==200 and status==200,"document-correspondence":results.get("a")=="OMEGA" and results.get("c")=="CHARLIE","valid-documents-retained":set(results)=={"a","c"},"repeated-id-last-wins":results.get("a")=="OMEGA" and "b" in failures}
    return {"fixture_version":"ext02-heldout/v1","checks":checks,"diagnostics":{k:"HTTP output failed held-out contract" for k,v in checks.items() if not v},"pass":all(checks.values())}
