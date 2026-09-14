from __future__ import annotations
import unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_ext02_admission import matrix as ext_matrix
from scripts.run_tool01_admission import matrix as tool_matrix
class ExtToolAdmissionTests(unittest.TestCase):
 def test_ext02_and_tool01_controls_and_resets(self):
  for matrix in (ext_matrix(),tool_matrix()):
   values={row["variant"]:row for row in matrix["matrix"]}
   self.assertFalse(values["baseline"]["pass"]);self.assertTrue(values["reference"]["pass"]);self.assertTrue(values["alternative"]["pass"])
   self.assertTrue(all(row["pass"] for row in matrix["reference_resets"]))
   self.assertTrue(all(not row["pass"] for key,row in values.items() if key not in {"baseline","reference","alternative"}))
