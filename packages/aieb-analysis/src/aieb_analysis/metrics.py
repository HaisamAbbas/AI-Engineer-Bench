"""Authoritative local aggregate calculations; reports consume these outputs."""
from __future__ import annotations
from dataclasses import dataclass
from math import sqrt, comb
from statistics import median
from collections import defaultdict

@dataclass(frozen=True)
class TrialObservation:
 task_id:str; family_id:str; project_id:str; entrant_id:str; repetition:int
 passed:bool|None; execution_valid:bool; engineer_cost_usd:str|None=None
 dev_application_cost_usd:str|None=None; verifier_cost_usd:str|None=None
 engineer_seconds:int|None=None; deadline:bool|None=None; category:str|None=None

def _deadline_rate(observations)->float|None:
 # All-attempt rate: incomplete evidence cannot silently become a known zero
 # or a rate over only the known subset.
 if not observations or any(v.deadline is None for v in observations):return None
 return sum(v.deadline for v in observations)/len(observations)


def wilson_interval(successes:int,total:int,z:float=1.959963984540054)->tuple[float,float]|None:
 if total<=0:return None
 p=successes/total;d=1+z*z/total;c=(p+z*z/(2*total))/d;h=z*sqrt((p*(1-p)+z*z/(4*total))/total)/d
 return (c-h,c+h)

def summarize(observations:tuple[TrialObservation,...], *, required_repetitions:int|None=None, fixed_task_weights:dict[str,float]|None=None, planned_cells:frozenset[tuple[str,str]]|None=None)->dict[str,object]:
 # `incomplete` must count VALID, resolved trials per cell, not raw attempts:
 # an infrastructure-invalid attempt is not a scored outcome, so a cell with
 # required_repetitions raw observations but fewer valid ones is still an
 # incomplete plan, not a complete one with a smaller n. A task/entrant pair
 # with ZERO trials has no key in `cells` at all and is invisible to a count
 # check either way; `planned_cells` (when the caller knows the frozen
 # matrix) closes that separate gap by checking for the cell's absence
 # explicitly, not inferring it from what happens to be present.
 cells=defaultdict(list)
 for item in observations: cells[(item.task_id,item.entrant_id)].append(item)
 per_task={}
 for (task,entrant),values in sorted(cells.items()):
  valid=[v for v in values if v.execution_valid and v.passed is not None]; s=sum(v.passed for v in valid);n=len(valid);k=required_repetitions or n
  per_task[f"{task}:{entrant}"]={"s":s,"n":n,"rate":None if not n else s/n,"wilson_95":wilson_interval(s,n),"all_k":None if n<k else s==k,"pass_power_k":None if n<k else comb(s,k)/comb(n,k)}
 incomplete=required_repetitions is not None and any(value["n"]!=required_repetitions for value in per_task.values())
 if planned_cells is not None: incomplete=incomplete or any(cell not in cells for cell in planned_cells)
 rates=[value for value in per_task.values() if value["rate"] is not None]
 weights=fixed_task_weights or {}; weighted=None
 if rates:
  weighted=sum(value["rate"]*weights.get(key.split(":")[0],1) for key,value in per_task.items() if value["rate"] is not None)/sum(weights.get(key.split(":")[0],1) for key,value in per_task.items() if value["rate"] is not None)
 valid=[v for v in observations if v.execution_valid]
 # A "scored" trial is execution_valid AND has a verdict (passed is not
 # None) - the same population per_task's own `valid` already uses. Not
 # every execution_valid observation is scored: an unresolved/indeterminate
 # evaluation can be execution_valid=True with passed=None (still awaiting
 # a verdict), and its cost must not dilute cost_per_resolution any more
 # than an infrastructure-invalid attempt's would.
 scored=[v for v in valid if v.passed is not None]
 successes=[v for v in scored if v.passed]
 # Cost per resolution's numerator is "engineer + development-application
 # cost for all SCORED trials" (spec) - an infrastructure-invalid, cancelled,
 # or still-unresolved attempt was never a scored outcome, so its cost must
 # not dilute this metric, even though it is real campaign spend.
 # Missing-accounting (any None) is checked only within that scored
 # population too: a missing cost on an excluded attempt must not make an
 # otherwise-complete scored numerator report unavailable.
 scored_costs=[]
 for v in scored:
  if v.engineer_cost_usd is None or v.dev_application_cost_usd is None: scored_costs=None;break
  scored_costs.append(float(v.engineer_cost_usd)+float(v.dev_application_cost_usd))
 cost_resolution=None if not successes or scored_costs is None else sum(scored_costs)/len(successes)
 # Spec: "report verifier and infrastructure expenses separately; publish
 # total campaign cost including invalid attempts" - total_campaign_cost_usd
 # is a genuine grand total across every attempt (engineer + dev-application
 # + verifier cost), not just the two fields cost_per_resolution uses; a
 # prior version summed only engineer/dev-application cost under this name,
 # which understated the actual total and mislabeled a partial figure as
 # the whole. verifier_cost_total_usd remains its own separately reported
 # breakdown alongside it (there is no infrastructure-cost field to include
 # yet - TrialObservation has none).
 all_costs=[]
 for v in observations:
  if v.engineer_cost_usd is None or v.dev_application_cost_usd is None or v.verifier_cost_usd is None: all_costs=None;break
  all_costs.append(float(v.engineer_cost_usd)+float(v.dev_application_cost_usd)+float(v.verifier_cost_usd))
 total_campaign_cost=None if all_costs is None else sum(all_costs)
 verifier_costs=[]
 for v in observations:
  if v.verifier_cost_usd is None: verifier_costs=None;break
  verifier_costs.append(float(v.verifier_cost_usd))
 verifier_cost_total=None if verifier_costs is None else sum(verifier_costs)
 times=sorted(v.engineer_seconds for v in successes if v.engineer_seconds is not None)

 def _weighted_over(pairs:list[tuple[str,float]])->float|None:
  filtered=[(task,rate) for task,rate in pairs if rate is not None]
  if not filtered:return None
  total_weight=sum(weights.get(task,1) for task,_ in filtered)
  if not total_weight:return None
  return sum(rate*weights.get(task,1) for task,rate in filtered)/total_weight

 # The results table needs a rate per entrant (across that entrant's own
 # task cells) and, per entrant again, a rate per task category - not a
 # category rate blended across every competing entrant, which would let one
 # entrant's performance leak into another's reported number. Both are
 # derivable from the same per_task cells already computed above.
 entrants=sorted({e for _,e in cells})
 per_entrant={}
 for entrant in entrants:
  per_entrant[entrant]=_weighted_over([(task,per_task[f"{task}:{e}"]["rate"]) for task,e in cells if e==entrant])

 # Per-entrant breakdowns of every suite-wide metric below (cost, time,
 # deadline rate, infrastructure attrition, valid/resolved task counts) -
 # a results table needs these AS entrant rows, not only one blended
 # suite-wide number repeated on every row (review finding #2). Each
 # mirrors the exact same population/exclusion rule its suite-wide
 # counterpart above already uses, just filtered to one entrant's own
 # observations - not a new methodology, the same one applied per-entrant.
 per_entrant_valid_trials:dict[str,int]={}
 per_entrant_resolved_tasks:dict[str,int]={}
 per_entrant_total_tasks:dict[str,int]={}
 per_entrant_cost_per_resolution:dict[str,float|None]={}
 per_entrant_verifier_cost_usd:dict[str,float|None]={}
 per_entrant_median_engineering_seconds:dict[str,int|None]={}
 per_entrant_deadline_rate:dict[str,float|None]={}
 per_entrant_infrastructure_attrition:dict[str,float|None]={}
 for entrant in entrants:
  entrant_cells=[per_task[f"{task}:{e}"] for task,e in cells if e==entrant]
  per_entrant_valid_trials[entrant]=sum(cell["n"] for cell in entrant_cells)
  per_entrant_resolved_tasks[entrant]=sum(1 for cell in entrant_cells if cell["all_k"])
  per_entrant_total_tasks[entrant]=len(entrant_cells)

  entrant_obs=[v for v in observations if v.entrant_id==entrant]
  entrant_valid=[v for v in entrant_obs if v.execution_valid]
  entrant_scored=[v for v in entrant_valid if v.passed is not None]
  entrant_successes=[v for v in entrant_scored if v.passed]
  entrant_scored_costs=[]
  for v in entrant_scored:
   if v.engineer_cost_usd is None or v.dev_application_cost_usd is None: entrant_scored_costs=None;break
   entrant_scored_costs.append(float(v.engineer_cost_usd)+float(v.dev_application_cost_usd))
  per_entrant_cost_per_resolution[entrant]=None if not entrant_successes or entrant_scored_costs is None else sum(entrant_scored_costs)/len(entrant_successes)
  entrant_verifier_costs=[]
  for v in entrant_obs:
   if v.verifier_cost_usd is None: entrant_verifier_costs=None;break
   entrant_verifier_costs.append(float(v.verifier_cost_usd))
  per_entrant_verifier_cost_usd[entrant]=None if entrant_verifier_costs is None else sum(entrant_verifier_costs)
  entrant_times=sorted(v.engineer_seconds for v in entrant_successes if v.engineer_seconds is not None)
  per_entrant_median_engineering_seconds[entrant]=None if not entrant_times else median(entrant_times)
  per_entrant_deadline_rate[entrant]=_deadline_rate(entrant_obs)
  per_entrant_infrastructure_attrition[entrant]=None if not entrant_obs else 1-len(entrant_valid)/len(entrant_obs)

 task_category={}
 for item in observations:
  if item.category is not None: task_category.setdefault(item.task_id,item.category)
 per_category=None
 if task_category:
  per_category={}
  for category in sorted(set(task_category.values())):
   tasks_in_category={task for task,c in task_category.items() if c==category}
   per_category[category]={
    entrant:_weighted_over([(task,per_task[f"{task}:{e}"]["rate"]) for task,e in cells if e==entrant and task in tasks_in_category])
    for entrant in entrants
   }

 return {"schema_version":"aieb.analysis/v1","required_repetitions":required_repetitions,"per_task":per_task,"per_entrant":per_entrant,"per_category":per_category,"complete_for_rank":not incomplete,"suite_rate":None if incomplete else weighted,"cost_per_resolution":cost_resolution,"total_campaign_cost_usd":total_campaign_cost,"verifier_cost_total_usd":verifier_cost_total,"successful_engineering_median_seconds":None if not times else median(times),"deadline_rate":_deadline_rate(observations),"infrastructure_attrition":None if not observations else 1-len(valid)/len(observations),"per_entrant_valid_trials":per_entrant_valid_trials,"per_entrant_resolved_tasks":per_entrant_resolved_tasks,"per_entrant_total_tasks":per_entrant_total_tasks,"per_entrant_cost_per_resolution":per_entrant_cost_per_resolution,"per_entrant_verifier_cost_usd":per_entrant_verifier_cost_usd,"per_entrant_median_engineering_seconds":per_entrant_median_engineering_seconds,"per_entrant_deadline_rate":per_entrant_deadline_rate,"per_entrant_infrastructure_attrition":per_entrant_infrastructure_attrition,"limitations":["project/family paired resampling is exploratory with fewer than six projects","campaign-level completeness checks the frozen planned cells when the caller supplies planned_cells (aggregate_campaign_snapshot wires it from the frozen manifest); a per-entrant valid-vs-planned coverage COUNT is still not broken out as its own field","per-entrant aggregate Wilson uncertainty is not computed - only per-task intervals (per_task[...].wilson_95) are available, since pooling per-task confidence intervals into one entrant-level interval is not statistically valid without a declared hierarchical model"]}

def paired_project_difference(observations:tuple[TrialObservation,...], left:str, right:str)->dict[str,object]:
 """Paired task-cell difference, grouped by underlying project (not fake IID runs)."""
 cells=defaultdict(dict)
 for item in observations:
  if item.execution_valid and item.passed is not None and item.entrant_id in {left,right}: cells[(item.project_id,item.task_id,item.repetition)][item.entrant_id]=item.passed
 paired=[(key,values[left]-values[right]) for key,values in cells.items() if left in values and right in values]
 if not paired:return {"paired_cells":0,"difference":None,"by_project":{},"limitation":"no common valid task/fixture schedule"}
 projects=defaultdict(list)
 for (project,_,_),difference in paired:projects[project].append(difference)
 return {"paired_cells":len(paired),"difference":sum(d for _,d in paired)/len(paired),"by_project":{p:sum(v)/len(v) for p,v in projects.items()},"limitation":"project-clustered resampling is exploratory with fewer than six projects"}
