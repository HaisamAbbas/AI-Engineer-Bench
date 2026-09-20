"""ENG-020 gap 6 (spec section 39): `GET /metrics` - a Prometheus text-exposition-format
(0.0.4) scrape endpoint for this API process.

Authenticated-safe (not public): gated behind the SAME `require_role` dependency every
other operator/admin-facing route in this app already uses, not a bespoke scheme -
metrics here include operational signals (kill-switch state, per-campaign infrastructure
failure counts, worker identities) that spec section 3 already treats as
operator/reviewer/administrator-only ("raw run artifacts as assigned"), the same bar
`kill_switch.py`'s GET route applies. This is a real, working exporter in this
repository; it does not mean any Prometheus server, Alertmanager instance, or paging
pipeline is deployed anywhere (see `worker/metrics.py`'s module docstring and
`deploy/alerts/prometheus-rules.yml`'s header).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..auth import Identity, require_role
from ..db import get_session
from ..worker import metrics, metrics_queries

router = APIRouter(tags=["metrics"])

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics")
def get_metrics(
    identity: Identity = Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> Response:
    """Scrape endpoint: computes every DB-backed gauge fresh (kill switch state,
    per-campaign consecutive infrastructure failures, worker heartbeat ages, active
    budget reservation ages) and renders it alongside the in-process running counters
    (`aieb_reconciler_worker_artifacts_purged_total`,
    `aieb_attempt_infrastructure_invalid_total`) as Prometheus exposition-format text."""
    metrics.replace_gauge_family(
        "aieb_kill_switch_active", [({}, metrics_queries.kill_switch_active(session))]
    )
    metrics.replace_gauge_family(
        "aieb_campaign_consecutive_infrastructure_failures",
        metrics_queries.campaign_consecutive_infrastructure_failures(session),
    )
    metrics.replace_gauge_family(
        "aieb_worker_heartbeat_age_seconds", metrics_queries.worker_heartbeat_ages(session)
    )
    metrics.replace_gauge_family(
        "aieb_budget_reservation_age_seconds", metrics_queries.active_budget_reservation_ages(session)
    )
    metrics.replace_counter_family(
        "aieb_reconciler_worker_artifacts_purged_total",
        metrics_queries.persistent_counter(session, "aieb_reconciler_worker_artifacts_purged_total"),
    )
    metrics.replace_counter_family(
        "aieb_attempt_infrastructure_invalid_total",
        metrics_queries.persistent_counter(session, "aieb_attempt_infrastructure_invalid_total"),
    )
    return Response(content=metrics.render_prometheus_text(), media_type=PROMETHEUS_CONTENT_TYPE)
