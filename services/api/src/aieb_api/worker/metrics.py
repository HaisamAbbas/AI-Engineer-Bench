"""Structured JSON log events and in-process counters (spec section 39).

No exported OpenTelemetry spans/metrics backend is wired up here - that is
real hosted-observability infrastructure work, not this ticket's scope.
Every event carries the IDs section 39 names (campaign/trial/attempt) so a
real exporter can be bolted on later without changing call sites.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from typing import Any

counters: Counter[str] = Counter()


def log_event(event: str, **fields: Any) -> None:
    counters[event] += 1
    record = {"event": event, "ts": time.time(), **fields}
    print(json.dumps(record, sort_keys=True, default=str), file=sys.stderr, flush=True)


def snapshot() -> dict[str, int]:
    return dict(counters)
