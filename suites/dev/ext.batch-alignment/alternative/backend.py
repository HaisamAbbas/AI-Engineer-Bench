def process(documents):
    ordered={}
    for item in documents: ordered[item["id"]]=item
    return {"results":[{"id":i,"value":d["text"].upper()} for i,d in ordered.items() if not d.get("fail")],"failures":[{"id":i,"reason":"declared failure"} for i,d in ordered.items() if d.get("fail")]}
