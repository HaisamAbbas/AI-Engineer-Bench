# EXT-04: Retain valid partial-batch outputs

Repair batch extraction so one malformed item cannot discard valid results before or after it.
Return per-item failures and preserve all successful outputs.
