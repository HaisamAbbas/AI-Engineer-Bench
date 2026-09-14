import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tests.maintainer.ext01.evaluator import evaluate
from tests.maintainer.task_admission import matrix
def run_matrix(): return matrix('ext.missingness','missingness_service',evaluate,[('baseline',None),('reference','reference/backend.py'),('alternative','alternative/backend.py'),('always-null','counterexamples/always-null.py')])
if __name__=='__main__': print(json.dumps(run_matrix(),indent=2,sort_keys=True))
