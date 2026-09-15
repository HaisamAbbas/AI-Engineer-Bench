"""Content digest for publication snapshots (aieb_analysis output).

Deliberately NOT aieb_core.canonical.content_hash: that function forbids
raw floats to keep benchmark-identity content (task/entrant/campaign
digests used for freezing/comparison) precision-unambiguous - the right
rule for content that gets compared/frozen across time. A published
snapshot is a different kind of artifact: aggregate statistics
(aieb_analysis.metrics.summarize()'s own output) that legitimately contain
floats (rates, costs), so hashing it through the float-forbidding
canonicalizer would make every snapshot with a real rate value permanently
undigestable. This still is a real self-consistency check - the same
function computes the digest at write time and re-verifies it at read time
(services/api/src/aieb_api/routes/results.py::_verified_snapshot) - it just
does not import aieb_core's stricter, differently-motivated restriction.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
