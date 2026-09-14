def process(record):
    """Broken: treats every unit as grams and rounds away declared precision."""
    return {"id":record["id"],"grams":round(float(record["value"])),"evidence":record["value"],"unit":record["unit"],"label":record.get("label")}
