`GET /health` returns ready. `POST /search` accepts `query`, `top_k`, and optional `metadata`; it returns at most `top_k` matching documents. Metadata filtering happens before ranking.
