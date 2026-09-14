def process(record):
    """Broken: absent values are manufactured as plausible defaults."""
    return {"id": record["id"], "amount": record.get("amount", 0), "currency": record.get("currency", "USD"), "note": record.get("note", "")}
