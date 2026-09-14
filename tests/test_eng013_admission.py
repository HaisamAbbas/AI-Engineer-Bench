from __future__ import annotations
import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_rag02_admission import matrix as rag02
from scripts.run_rag03_admission import matrix as rag03
from scripts.run_rag04_admission import matrix as rag04
from scripts.run_ext01_admission import run_matrix as ext01
from scripts.run_ext03_admission import run_matrix as ext03
from scripts.run_ext04_admission import run_matrix as ext04
from scripts.run_tool02_admission import run_matrix as tool02
from scripts.run_tool03_admission import run_matrix as tool03
from scripts.run_tool04_admission import run_matrix as tool04

class Eng013AdmissionTests(unittest.TestCase):
 def test_new_task_admission_controls_and_resets(self):
  for run in (rag02,rag03,rag04,ext01,ext03,ext04,tool02,tool03,tool04):
   report=run();cases={row['variant']:row for row in report['matrix']}
   self.assertFalse(cases['baseline']['pass'])
   self.assertTrue(cases['reference']['pass']);self.assertTrue(cases['alternative']['pass'])
   self.assertTrue(all(not row['pass'] for key,row in cases.items() if key not in {'baseline','reference','alternative'}))
   self.assertEqual(len(report['reference_resets']),10);self.assertTrue(all(row['pass'] for row in report['reference_resets']))
 def test_catalog_has_twelve_distinct_families_and_pending_reviews(self):
  catalog=json.loads((ROOT/'suites/dev/catalog.json').read_text(encoding='utf-8'))
  self.assertEqual(catalog['review_status'],'pending-independent-review');self.assertEqual(len(catalog['tasks']),12)
  self.assertGreaterEqual(len({task['family_id'] for task in catalog['tasks']}),6)
  for task in catalog['tasks']:self.assertTrue((ROOT/'suites/dev'/task['path']/'task.yaml').is_file())
