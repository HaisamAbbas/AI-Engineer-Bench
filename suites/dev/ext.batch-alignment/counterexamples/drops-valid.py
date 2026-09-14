def process(documents): return {"results":[{"id":d["id"],"value":d["text"].upper()} for d in documents[:1]],"failures":[]}
