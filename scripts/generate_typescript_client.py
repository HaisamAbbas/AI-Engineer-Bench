"""Regenerate the checked-in typed TypeScript client artifact for services/api
(ENG-014) from the checked-in OpenAPI schema.

Prompt 11 requires both "OpenAPI and typed client artifacts" as its own
deliverables - not something ENG-016 (the not-yet-built apps/web) owns.
Superseding DECISIONS.md ENG014-004: deferring this until apps/web exists
treated "no consumer yet" as a reason not to produce the artifact, but the
prompt asks for the artifact itself, independent of whether anything
consumes it yet - the same way openapi.json itself is checked in with no
consumer. `apps/web` (ENG-016) will import these generated types rather than
duplicating API definitions by hand (spec section 10/11), exactly as
ENG014-004 already anticipated for the consumption side.

Run `python scripts/generate_openapi.py` first if the API schema changed.
Requires Node.js/npx (network access to fetch `openapi-typescript` on first
run); this is a documented, manually-run regeneration step, not something
the Python test suite depends on.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    schema = ROOT / "docs/implementation/evidence/ENG-014/openapi.json"
    if not schema.exists():
        raise SystemExit(f"{schema} does not exist; run scripts/generate_openapi.py first")
    out = ROOT / "docs/implementation/evidence/ENG-014/api-client.d.ts"
    subprocess.run(
        ["npx", "--yes", "openapi-typescript", str(schema), "-o", str(out)],
        cwd=ROOT, check=True, shell=(sys.platform == "win32"),
    )
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
