# Embedding service API

`POST /search` accepts `query` and `embedding_version`. It returns compatible hits, or
returns HTTP 409 / an empty hit set when that embedding space is unavailable. A response
must never represent a hit from another embedding version as compatible.
