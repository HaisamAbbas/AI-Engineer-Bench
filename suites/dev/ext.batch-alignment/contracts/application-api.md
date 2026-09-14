# API

`GET /health` returns `{"status":"ready"}`. `POST /extract` accepts `{"documents":[{"id":str,"text":str,"fail":bool?}]}` and returns `{"results":[{"id", "value"}],"failures":[{"id","reason"}]}`. Output order is unspecified. Repeated IDs in one request use last occurrence wins; failed documents must not suppress valid documents.
