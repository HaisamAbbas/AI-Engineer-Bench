"""The API contract's idempotency audit (spec section 22/33, API-01).

"Mutating calls require ... idempotency keys." This test derives the list of
mutating operations from the generated OpenAPI schema rather than trusting a
hand-maintained list, so a new POST/PATCH/PUT/DELETE route that forgets its
`Idempotency-Key` header fails CI instead of shipping an unreplayable write.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

from aieb_api.app import create_app  # noqa: E402

# Mutating HTTP methods.
_MUTATING = ("post", "patch", "put", "delete")

# POST routes that persist NOTHING - reads expressed as POST because they take a
# request body. Each must be justified here; `preview` computes a matrix from
# the pinned registry and never writes (see campaigns.preview_matrix).
_NON_PERSISTING_POSTS = {
    "/v1/campaigns/{campaign_id}/preview",
    # Validates a presented scoped attempt credential and returns a plain
    # boolean (routes/attempts.py::verify_attempt_credential_endpoint) - a
    # POST only because it carries a token in the body, never a write to the
    # DB, so it has nothing to replay idempotently. First audited here
    # 2026-09-22 when this whole suite's real-PostgreSQL run first went green.
    "/v1/attempts/{attempt_id}/credentials/verify",
}


class IdempotencyAuditTests(unittest.TestCase):
    def test_every_mutating_route_declares_an_idempotency_key(self) -> None:
        schema = create_app().openapi()
        missing: list[str] = []
        audited: list[str] = []
        for path, operations in schema["paths"].items():
            for method, operation in operations.items():
                if method not in _MUTATING:
                    continue
                if method == "post" and path in _NON_PERSISTING_POSTS:
                    continue
                audited.append(f"{method.upper()} {path}")
                parameters = operation.get("parameters", [])
                declared = any(
                    parameter.get("in") == "header" and parameter.get("name") == "Idempotency-Key"
                    for parameter in parameters
                )
                if not declared:
                    missing.append(f"{method.upper()} {path}")
        self.assertEqual(missing, [], f"mutating routes without an Idempotency-Key: {missing}")
        # Guard against the audit silently matching nothing (e.g. a schema change).
        self.assertGreaterEqual(len(audited), 12, audited)

    def test_non_persisting_post_allowlist_is_real(self) -> None:
        schema = create_app().openapi()
        for path in _NON_PERSISTING_POSTS:
            self.assertIn(path, schema["paths"], f"stale non-persisting allowlist entry: {path}")
            self.assertIn("post", schema["paths"][path])


if __name__ == "__main__":
    unittest.main()
