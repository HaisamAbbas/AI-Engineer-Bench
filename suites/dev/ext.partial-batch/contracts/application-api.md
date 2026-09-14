# Partial-batch extraction API

`POST /extract` accepts `{items:[...]}`. A malformed item produces its own failure status;
every valid item in the same batch must still produce an output exactly once.
