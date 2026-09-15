"""Authoritative local aggregate calculations; reports consume these outputs."""
from __future__ import annotations
from dataclasses import dataclass
from math import sqrt, comb
from collections import defaultdict

@dataclass(frozen=True)
class TrialObservation:
 task_id:str; family_id:str; project_id:str; entrant_id:str; repetition:int
 passed:bool|None; execution_valid:bool; engineer_cost_usd:str|None=None
 dev_application_cost_usd:str|None=None; verifier_cost_usd:str|None=None
 engineer_seconds:int|None=None; deadline:bool=False

def wilson_interval(successes:int,total:int,z:float=1.959963984540054)->tuple[float,float]|None:
 if total<=0:return None
 p=successes/total;d=1+z*z/total;c=(p+z*z/(2*total))/d;h=z*sqrt((p*(1-p)+z*z/(4*total))/total)/d
 return (c-h,c+h)

def summarize(observations:tuple[TrialObservation,...], *, required_repetitions:int|None=None, fixed_task_weights:dict[str,float]|None=None, planned_cells:frozenset[tuple[str,str]]|None=None)->dict[str,object]:
 # `incomplete` from repetition counts alone only ever inspects cells that
 # already have at least one observation; a task/entrant pair with ZERO
 # trials has no key in `cells` and was silently invisible to that check.
 # `planned_cells` (when the caller knows the frozen matrix) closes that gap:
 # a wholly missing cell is checked explicitly, not inferred from what
 # happens to be present.
 cells=defaultdict(list)
 for item in observations: cells[(item.task_id,item.entrant_id)].append(item)
 incomplete=required_repetitions is not None and any(len(values)!=required_repetitions for values in cells.values())
 if planned_cells is not None: incomplete=incomplete or any(cell not in cells for cell in planned_cells)
 per_task={}
 for (task,entrant),values in sorted(cells.items()):
  valid=[v for v in values if v.execution_valid and v.passed is not None]; s=sum(v.passed for v in valid);n=len(valid);k=required_repetitions or n
  per_task[f"{task}:{entrant}"]={"s":s,"n":n,"rate":None if not n else s/n,"wilson_95":wilson_interval(s,n),"all_k":None if n<k else s==k,"pass_power_k":None if n<k else comb(s,k)/comb(n,k)}
 rates=[value for value in per_task.values() if value["rate"] is not None]
 weights=fixed_task_weights or {}; weighted=None
 if rates:
  weighted=sum(value["rate"]*weights.get(key.split(":")[0],1) for key,value in per_task.items() if value["rate"] is not None)/sum(weights.get(key.split(":")[0],1) for key,value in per_task.items() if value["rate"] is not None)
 successes=[v for v in observations if v.execution_valid and v.passed]
 costs=[]
 for v in observations:
  if v.engineer_cost_usd is None or v.dev_application_cost_usd is None: costs=None;break
  costs.append(float(v.engineer_cost_usd)+float(v.dev_application_cost_usd))
 cost_resolution=None if not successes or costs is None else sum(costs)/len(successes)
 valid=[v for v in observations if v.execution_valid]; times=sorted(v.engineer_seconds for v in successes if v.engineer_seconds is not None)
 return {"schema_version":"aieb.analysis/v1","per_task":per_task,"complete_for_rank":not incomplete,"suite_rate":None if incomplete else weighted,"cost_per_resolution":cost_resolution,"successful_engineering_median_seconds":None if not times else times[len(times)//2],"deadline_rate":None if not observations else sum(v.deadline for v in observations)/len(observations),"infrastructure_attrition":None if not observations else 1-len(valid)/len(observations),"limitations":["project/family paired resampling is exploratory with fewer than six projects"]}

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
