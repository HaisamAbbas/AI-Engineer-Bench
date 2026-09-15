from __future__ import annotations
import unittest
from aieb_analysis import TrialObservation,summarize,wilson_interval,paired_project_difference
class AnalysisTests(unittest.TestCase):
 def test_zero_success_unknown_cost_and_missing_cells_block_rank(self):
  rows=(TrialObservation("a","f","p","e",0,False,True,None,None),TrialObservation("a","f","p","e",1,False,True,None,None))
  result=summarize(rows,required_repetitions=3)
  self.assertFalse(result["complete_for_rank"]);self.assertIsNone(result["suite_rate"]);self.assertIsNone(result["cost_per_resolution"]);self.assertEqual(result["per_task"]["a:e"]["all_k"],None)
 def test_wilson_repeatability_and_attrition(self):
  rows=tuple(TrialObservation("a","family","project","e",i,i<2,True,"1","2",engineer_seconds=10,deadline=i==2) for i in range(3))
  result=summarize(rows,required_repetitions=3,fixed_task_weights={"a":2})
  self.assertEqual(result["per_task"]["a:e"]["pass_power_k"],0.0);self.assertEqual(result["cost_per_resolution"],4.5);self.assertEqual(result["deadline_rate"],1/3);self.assertIsNotNone(wilson_interval(2,3))
 def test_project_limitations_are_preserved(self):
  result=summarize((TrialObservation("a","f","project","e",0,True,False),),required_repetitions=1)
  self.assertEqual(result["infrastructure_attrition"],1.0);self.assertIn("project/family",result["limitations"][0])
 def test_paired_difference_requires_common_cells_and_groups_projects(self):
  rows=(TrialObservation("a","f","p", "left",0,True,True),TrialObservation("a","f","p","right",0,False,True))
  paired=paired_project_difference(rows,"left","right");self.assertEqual(paired["paired_cells"],1);self.assertEqual(paired["by_project"],{"p":1.0})
 def test_wholly_missing_planned_cell_blocks_rank(self):
  # Review finding #7: a task/entrant pair with ZERO observations has no key
  # in the internal cell map at all, so repetition-count checks alone never
  # see it. Without planned_cells, this incorrectly reports complete.
  self.assertTrue(summarize((),required_repetitions=3,fixed_task_weights={"missing":1})["complete_for_rank"])
  # With the frozen plan supplied, the missing cell is caught explicitly.
  result=summarize((),required_repetitions=3,fixed_task_weights={"missing":1},planned_cells=frozenset({("missing","entrant-a")}))
  self.assertFalse(result["complete_for_rank"]);self.assertIsNone(result["suite_rate"])
  # A partially-populated plan where every planned cell has observations
  # still resolves complete_for_rank from repetition counts as before.
  rows=tuple(TrialObservation("a","f","p","e",i,True,True) for i in range(3))
  result=summarize(rows,required_repetitions=3,planned_cells=frozenset({("a","e")}))
  self.assertTrue(result["complete_for_rank"])
