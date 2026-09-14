def extract(documents):
    """Broken: backend shuffles output but service reattaches by input position."""
    outputs = [{"value": item["text"].upper()} for item in reversed(documents) if not item.get("fail")]
    return outputs

def process(documents):
    outputs = extract(documents)
    return {"results": [{"id": item["id"], **output} for item, output in zip(documents, outputs)], "failures": []}
