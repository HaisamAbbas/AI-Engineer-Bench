# Idempotent write API

`POST /jobs` may retry an ambiguous tool response, but requested effects occur exactly once.
The operation is keyed by the request `id`; refusing all writes is not a valid repair.
