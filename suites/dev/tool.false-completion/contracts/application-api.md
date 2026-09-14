# API

`GET /health` returns ready. `POST /jobs` accepts `{"id":str,"payload":object}` and returns `{"id":str,"status":"completed"|"failed"}`. The application calls the external operation service configured by `OPERATION_URL`; the service ledger is authoritative.
