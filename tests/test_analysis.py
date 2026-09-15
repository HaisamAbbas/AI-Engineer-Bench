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
 def test_per_entrant_and_per_category_rates_are_reported_separately_from_suite_rate(self):
  # Review finding #8: summarize() only ever emitted one combined suite_rate
  # across every entrant/category together, with no way to recover a rate
  # for a single entrant or a single task category from the output.
  rows=(
   TrialObservation("a","f","p","left",0,True,True,category="rag"),
   TrialObservation("a","f","p","left",1,True,True,category="rag"),
   TrialObservation("b","f","p","left",0,True,True,category="tool"),
   TrialObservation("a","f","p","right",0,False,True,category="rag"),
   TrialObservation("b","f","p","right",0,False,True,category="tool"),
  )
  result=summarize(rows)
  self.assertEqual(result["per_entrant"]["left"],1.0)
  self.assertEqual(result["per_entrant"]["right"],0.0)
  self.assertEqual(result["per_category"]["rag"],{"left":1.0,"right":0.0})
  self.assertEqual(result["per_category"]["tool"],{"left":1.0,"right":0.0})
 def test_per_category_rate_is_reported_per_entrant_not_blended_across_entrants(self):
  # A prior version of per_category averaged every entrant's rate for a
  # category into one blended number, letting a strong entrant's category
  # performance leak into a weak entrant's reported rate (and vice versa).
  # Here "left" excels at rag but fails tool, and "right" is the reverse -
  # a blended per-category number would report identical, uninformative
  # rates for both categories; the correct output tells them apart.
  rows=(
   TrialObservation("a","f","p","left",0,True,True,category="rag"),
   TrialObservation("b","f","p","left",0,False,True,category="tool"),
   TrialObservation("a","f","p","right",0,False,True,category="rag"),
   TrialObservation("b","f","p","right",0,True,True,category="tool"),
  )
  result=summarize(rows)
  self.assertEqual(result["per_category"]["rag"],{"left":1.0,"right":0.0})
  self.assertEqual(result["per_category"]["tool"],{"left":0.0,"right":1.0})
 def test_per_category_is_none_when_no_observation_carries_a_category(self):
  result=summarize((TrialObservation("a","f","p","e",0,True,True),))
  self.assertIsNone(result["per_category"])
 def test_verifier_cost_reported_separately_and_unavailable_when_any_missing(self):
  # Review finding #8: verifier/infrastructure cost must be its own number,
  # not silently folded into (or dropped from) cost_per_resolution.
  rows=(TrialObservation("a","f","p","e",0,True,True,"1","2",verifier_cost_usd="3"),)
  self.assertEqual(summarize(rows)["verifier_cost_total_usd"],3.0)
  rows_missing=(TrialObservation("a","f","p","e",0,True,True,"1","2"),)
  self.assertIsNone(summarize(rows_missing)["verifier_cost_total_usd"])
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
