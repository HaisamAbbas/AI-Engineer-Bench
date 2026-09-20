"""Structured JSON log events, in-process counters, and a hand-rolled Prometheus text
exporter (spec section 39, ENG-020 gap 6).

This module is an IN-PROCESS, PULL-BASED exporter for whichever process imports it (the
API process via `GET /metrics`, or a worker process that chooses to expose the same
route). It is NOT a deployed monitoring stack: no Prometheus server, Alertmanager
instance, or paging pipeline is deployed anywhere in this environment (no cloud
budget/authorization; see ADR-12, DECISIONS.md "Official VM provider | Deferred", and
deploy/alerts/prometheus-rules.yml's own header). What exists here is real, working
code that produces valid Prometheus exposition-format (0.0.4) text on demand - a real
Prometheus server, if one were ever deployed, could scrape it and evaluate the alert
rules in deploy/alerts/prometheus-rules.yml against real metric names. Nothing beyond
that is claimed.

`prometheus_client` is intentionally NOT used (it is not a declared dependency of this
package and none is added by this module): the exposition format is simple enough
(`# HELP`, `# TYPE`, then `name{labels} value` lines) to hand-roll directly, which is
what `render_prometheus_text()` below does.

Two kinds of metric live here, matching how each is semantically correct to produce:

- Gauges that must reflect TRUE CURRENT DATABASE STATE (kill switch active/inactive,
  per-campaign consecutive infrastructure failures, worker heartbeat age, budget
  reservation age) are never tracked as module-level state - a freshly started API
  process would otherwise report stale or empty values until enough events replayed
  into it. Instead, `worker/metrics_queries.py` computes them fresh from the database
  at scrape time, and the scrape route (`routes/metrics.py`) calls
  `replace_gauge_family()` to load each one into this registry immediately before
  rendering.
- Counters that are naturally monotonic running totals since process start
  (`aieb_reconciler_worker_artifacts_purged_total`, `aieb_attempt_infrastructure_invalid_total`)
  are incremented in-process at their real call sites via `inc_counter()`, the same
  pattern `log_event`'s own `counters` already used.

`log_event`/`counters`/`snapshot()` are unchanged and still imported by
`worker/reconciler.py` and `worker/loop.py` - every event still carries the IDs section
39 names (campaign/trial/attempt) so this exporter (or a future hosted one) can be
extended without changing those call sites.
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


# ---------------------------------------------------------------------------
# Prometheus exposition-format registry
# ---------------------------------------------------------------------------

LabelKey = tuple[tuple[str, str], ...]

# name -> {sorted label tuple -> value}
_gauges: dict[str, dict[LabelKey, float]] = {}
_metric_counters: dict[str, dict[LabelKey, float]] = {}

# Declared metric metadata so `# HELP`/`# TYPE` lines are emitted even before the first
# value is ever recorded (a freshly started process still exposes a well-formed, if
# empty, metric family) and so every alert rule's metric name is guaranteed present.
_METRIC_METADATA: dict[str, tuple[str, str]] = {
    "aieb_worker_heartbeat_age_seconds": (
        "gauge",
        "Seconds since a leased work item's lease was last extended (heartbeat), "
        "computed live from the current lease_expiry. Spec section 39's <90s detection target.",
    ),
    "aieb_campaign_consecutive_infrastructure_failures": (
        "gauge",
        "Current consecutive-infrastructure-failure count for a non-terminal campaign "
        "(repository.AUTO_PAUSE_INFRASTRUCTURE_FAILURE_THRESHOLD auto-pauses at 3).",
    ),
    "aieb_budget_reservation_age_seconds": (
        "gauge",
        "Age in seconds of a campaign's budget reservation, labeled by its current status.",
    ),
    "aieb_reconciler_worker_artifacts_purged_total": (
        "counter",
        "Total staging worker_artifact_blob rows purged by the reconciler's orphan sweep.",
    ),
    "aieb_kill_switch_active": (
        "gauge",
        "1 if the global spend/dispatch kill switch is active, 0 otherwise.",
    ),
    "aieb_attempt_infrastructure_invalid_total": (
        "counter",
        "Total attempts finalized with terminal_status=infrastructure_invalid.",
    ),
}


def _label_key(labels: dict[str, str]) -> LabelKey:
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def set_gauge(name: str, value: float, **labels: str) -> None:
    """Set one label-series of a gauge metric to `value`, leaving any other
    label-series of the same metric untouched. Prefer `replace_gauge_family`
    for gauges computed fresh from the database at scrape time, so a series
    whose labels no longer apply (a campaign that went terminal, a lease that
    was released) does not linger with a stale value forever."""
    _gauges.setdefault(name, {})[_label_key(labels)] = float(value)


def replace_gauge_family(name: str, series: list[tuple[dict[str, str], float]]) -> None:
    """Replace an ENTIRE gauge metric's set of label-series at once - the correct way
    to publish a gauge computed live from the database, since the previous scrape's
    series (e.g. a campaign that has since gone terminal, or a lease that finished)
    must not linger in the exposition text with a now-meaningless stale value."""
    _gauges[name] = {_label_key(labels): float(value) for labels, value in series}


def inc_counter(name: str, amount: float = 1, **labels: str) -> None:
    """Increment one label-series of an in-process running counter. Used at real call
    sites (reconciler purge count, infrastructure-invalid finalize) exactly like
    `log_event`'s own `counters`, and - like that counter - resets to zero on process
    restart; that is an accurate description of an in-process counter, not a defect to
    paper over with persistence this ticket does not add."""
    if amount < 0:
        raise ValueError("counters may only increase")
    key = _label_key(labels)
    family = _metric_counters.setdefault(name, {})
    family[key] = family.get(key, 0.0) + amount


def _format_value(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return repr(float(value))


def _format_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_series(name: str, family: dict[LabelKey, float]) -> list[str]:
    lines = []
    for label_key, value in sorted(family.items()):
        if label_key:
            label_str = "{" + ",".join(f'{k}="{_format_label_value(v)}"' for k, v in label_key) + "}"
        else:
            label_str = ""
        lines.append(f"{name}{label_str} {_format_value(value)}")
    return lines


def render_prometheus_text() -> str:
    """Render every known metric as Prometheus text exposition format (0.0.4):
    a `# HELP`/`# TYPE` pair per metric name, then one `name{labels} value` line per
    label-series currently recorded. Every metric name deploy/alerts/prometheus-rules.yml
    references appears here (with its `# HELP`/`# TYPE` lines) even with zero recorded
    series, so a real Prometheus scrape target's metadata listing is always complete."""
    names = sorted(set(_METRIC_METADATA) | set(_gauges) | set(_metric_counters))
    lines: list[str] = []
    for name in names:
        kind, help_text = _METRIC_METADATA.get(
            name, ("gauge" if name in _gauges else "counter", "")
        )
        lines.append(f"# HELP {name} {help_text}".rstrip())
        lines.append(f"# TYPE {name} {kind}")
        family = (_gauges if kind == "gauge" else _metric_counters).get(name, {})
        lines.extend(_format_series(name, family))
    return "\n".join(lines) + "\n"
