def process(record):
    return {"id": record["id"], **{key: record[key] for key in ("amount", "currency", "note") if key in record}}
