# Missingness extraction API

`POST /extract` accepts one JSON record and returns its `id` plus only fields actually
present in the source. Missing is distinct from `0`, `""`, and `null`; no defaults may be invented.
