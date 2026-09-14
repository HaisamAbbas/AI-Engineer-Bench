def process(record):
    result={"id":record["id"]}
    for key in ("amount","currency","note"):
        if key in record: result[key]=record[key]
    return result
