def process(documents):
    latest = {item["id"]: item for item in documents}
    results=[]; failures=[]
    for item in reversed(tuple(latest.values())):
        if item.get("fail"): failures.append({"id":item["id"],"reason":"declared failure"})
        else: results.append({"id":item["id"],"value":item["text"].upper()})
    return {"results":results,"failures":failures}
