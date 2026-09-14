# ENG-011 analysis evidence

`aieb-analysis` supplies the single local metric implementation used by downstream reports. It emits per-task `s/n`, Wilson 95% intervals, all-k and `C(s,k)/C(n,k)` repeatability, fixed-weight suite rate only for complete plans, cost per resolution, successful engineering median, deadline rate, and infrastructure attrition.

Unknown costs remain unavailable. Zero successes produces undefined cost per resolution. Missing planned repetitions blocks a canonical complete ranking. Outputs retain a project/family clustered-analysis limitation rather than fabricating universal intervals from the three development projects.
