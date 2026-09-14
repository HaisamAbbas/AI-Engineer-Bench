# EXT-02: Preserve document/output correspondence

Repair the batch extraction service. Output order is not stable and documents may fail independently. The public API must return each successful result with its correct document ID, retain all valid documents, and apply the declared repeated-ID rule: the last occurrence in one request wins.
