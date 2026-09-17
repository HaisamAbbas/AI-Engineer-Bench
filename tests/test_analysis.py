from __future__ import annotations
import unittest
from aieb_analysis import TrialObservation,summarize,wilson_interval,paired_project_difference
class AnalysisTests(unittest.TestCase):
 def test_deadline_unknown_propagates_without_hiding_known_entrant_rates(self):
  rows=(
   TrialObservation("a","f","p","known",0,True,True,deadline=False),
   TrialObservation("a","f","p","known",1,True,True,deadline=True),
   TrialObservation("a","f","p","unknown",0,True,True),
  )
  result=summarize(rows)
  self.assertIsNone(result["deadline_rate"])
  self.assertEqual(result["per_entrant_deadline_rate"],{"known":0.5,"unknown":None})
  self.assertIsNone(summarize(())["deadline_rate"])
  self.assertEqual(summarize(rows[:1])["deadline_rate"],0.0)
  mixed=(rows[0],TrialObservation("b","f","p","known",0,True,True,deadline=None))
  self.assertIsNone(summarize(mixed)["per_entrant_deadline_rate"]["known"])


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
 def test_per_entrant_breakdowns_of_cost_time_deadline_and_attrition_are_not_blended(self):
  # Review finding #2: a results table needs cost, verifier cost, median
  # engineering time, deadline rate, and infrastructure attrition AS
  # per-entrant rows, not one suite-wide number repeated on every row.
  # "left" is a cheap, fast, reliable entrant; "right" is expensive, slow,
  # deadline-prone, and has an infrastructure-invalid attempt - a blended
  # suite-wide number would report identical, uninformative figures for
  # both; the correct output tells them apart.
  rows=(
   TrialObservation("a","f","p","left",0,True,True,"1","1",engineer_seconds=10,deadline=False),
   TrialObservation("b","f","p","left",0,True,True,"1","1",engineer_seconds=20,deadline=False),
   TrialObservation("a","f","p","right",0,True,True,"50","50",engineer_seconds=1000,deadline=True),
   TrialObservation("b","f","p","right",0,None,False,deadline=False),  # infrastructure-invalid attempt
  )
  result=summarize(rows)
  self.assertEqual(result["per_entrant_cost_per_resolution"]["left"],2.0)
  self.assertEqual(result["per_entrant_cost_per_resolution"]["right"],100.0)
  self.assertEqual(result["per_entrant_median_engineering_seconds"]["left"],15.0)
  self.assertEqual(result["per_entrant_deadline_rate"]["left"],0.0)
  self.assertEqual(result["per_entrant_deadline_rate"]["right"],0.5)
  self.assertEqual(result["per_entrant_infrastructure_attrition"]["left"],0.0)
  self.assertEqual(result["per_entrant_infrastructure_attrition"]["right"],0.5)
  self.assertEqual(result["per_entrant_valid_trials"]["left"],2)
  self.assertEqual(result["per_entrant_valid_trials"]["right"],1)
  self.assertEqual(result["per_entrant_total_tasks"]["left"],2)
  self.assertEqual(result["per_entrant_total_tasks"]["right"],2)
 def test_median_engineering_time_is_a_real_median_not_the_upper_middle_value(self):
  # Review finding #3 (second pass): [10, 20] was reported as 20 (picking
  # times[len(times)//2], the upper-middle raw value for an even sample),
  # not the conventional median (15). A prior test even asserted 20,
  # locking the defect in as expected behavior.
  rows=(
   TrialObservation("a","f","p","e",0,True,True,engineer_seconds=10),
   TrialObservation("b","f","p","e",0,True,True,engineer_seconds=20),
  )
  self.assertEqual(summarize(rows)["successful_engineering_median_seconds"],15.0)
  # An odd sample still returns the middle value, as a median always should.
  rows_odd=(
   TrialObservation("a","f","p","e",0,True,True,engineer_seconds=10),
   TrialObservation("b","f","p","e",0,True,True,engineer_seconds=20),
   TrialObservation("c","f","p","e",0,True,True,engineer_seconds=30),
  )
  self.assertEqual(summarize(rows_odd)["successful_engineering_median_seconds"],20)
 def test_required_repetitions_is_echoed_for_labeling_all_k(self):
  # Review finding #2: the frontend needs the actual k value to label
  # "all-5" rather than a generic "all-k".
  self.assertEqual(summarize((),required_repetitions=5)["required_repetitions"],5)
  self.assertIsNone(summarize(())["required_repetitions"])
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
 def test_cost_per_resolution_excludes_infrastructure_invalid_attempts(self):
  # Fourth independent review, confirmed by direct reproduction: cost per
  # resolution's numerator ("engineer + development-application cost for
  # all SCORED trials", spec) previously summed every observation's cost
  # regardless of execution_valid, so one expensive infrastructure-invalid
  # attempt inflated the reported per-resolution cost even though it was
  # never a scored outcome. total_campaign_cost_usd is the separate,
  # deliberately broader figure the spec also requires ("publish total
  # campaign cost including invalid attempts") - a genuine grand total
  # (engineer + dev-application + verifier), so it needs verifier_cost_usd
  # on every observation to be available, unlike cost_per_resolution.
  rows=(
   TrialObservation("a","f","p","e",0,True,True,"1","2",verifier_cost_usd="0.5"),  # valid success, cost 3
   TrialObservation("a","f","p","e",1,None,False,"100","100",verifier_cost_usd="0.5"),  # infra-invalid, cost 200
  )
  result=summarize(rows)
  self.assertEqual(result["cost_per_resolution"],3.0)
  self.assertEqual(result["total_campaign_cost_usd"],204.0)
 def test_cost_per_resolution_excludes_unresolved_valid_evaluations(self):
  # Fifth independent review, confirmed by direct reproduction: an
  # execution_valid observation with passed=None (an indeterminate/
  # unresolved evaluation, not yet a scored verdict) was still counted in
  # cost_per_resolution's numerator, on the theory that "valid" alone was
  # the scored population - but per_task's own definition of a scored
  # trial is execution_valid AND passed is not None, and cost_per_resolution
  # must use that same population, not a looser one.
  rows=(
   TrialObservation("a","f","p","e",0,True,True,"1","2"),  # scored success, cost 3
   TrialObservation("a","f","p","e",1,None,True,"100","100"),  # valid but unresolved, cost 200
  )
  result=summarize(rows)
  self.assertEqual(result["cost_per_resolution"],3.0)
 def test_cost_per_resolution_unaffected_by_missing_cost_on_an_excluded_invalid_attempt(self):
  # Missing-accounting checks apply only to the scored numerator population:
  # an invalid attempt with no cost data at all must not make an otherwise-
  # complete scored cost_per_resolution report unavailable.
  rows=(
   TrialObservation("a","f","p","e",0,True,True,"1","2"),
   TrialObservation("a","f","p","e",1,None,False,None,None),
  )
  result=summarize(rows)
  self.assertEqual(result["cost_per_resolution"],3.0)
  self.assertIsNone(result["total_campaign_cost_usd"])
 def test_a_cell_with_enough_raw_attempts_but_not_enough_valid_ones_blocks_rank(self):
  # Second independent review, confirmed by direct reproduction: completeness
  # previously counted raw observations per cell, not valid/resolved ones. A
  # cell with required_repetitions raw attempts but an infrastructure-invalid
  # one among them (not a scored outcome) was reported complete with a
  # smaller n instead of incomplete - exactly the gap planned_cells closes
  # for a wholly MISSING cell, but for a cell that has attempts, just not
  # enough valid ones.
  rows=(
   TrialObservation("a","f","p","e",0,True,True),
   TrialObservation("a","f","p","e",1,True,True),
   TrialObservation("a","f","p","e",2,None,False),  # infrastructure-invalid: not a scored outcome
  )
  result=summarize(rows,required_repetitions=3)
  self.assertFalse(result["complete_for_rank"])
  self.assertIsNone(result["suite_rate"])
  self.assertEqual(result["per_task"]["a:e"]["n"],2)
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
