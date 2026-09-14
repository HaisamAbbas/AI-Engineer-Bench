# TOOL-02: Deduplicate ambiguous write retries

An operation can commit then lose its response. Repair the retry path so it resolves the
requested operation without duplicating its external effect.
