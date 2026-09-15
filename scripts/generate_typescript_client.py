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
Requires Node.js/npx (network access to fetch the pinned `openapi-typescript`
release on first run - npx caches it locally after that); this is a
documented, manually-run regeneration step, not something the Python test
suite depends on.

The generator version is pinned (`GENERATOR_SPEC` below) rather than left as
a bare `openapi-typescript`: an unpinned `npx` can silently fetch whatever the
latest published release happens to be on a given day and produce different
output for the same schema, defeating "reproducible artifact" the same way an
unpinned dependency would (spec section 10: "Do not specify 'latest'
dependencies"). Run with `--check` (used in CI) to fail if the checked-in
file does not match what the pinned generator produces right now, instead of
silently drifting.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_SPEC = "openapi-typescript@7.13.0"


def generate(schema: Path, out: Path) -> None:
    subprocess.run(
        ["npx", "--yes", GENERATOR_SPEC, str(schema), "-o", str(out)],
        cwd=ROOT, check=True, shell=(sys.platform == "win32"),
    )


def main() -> None:
    schema = ROOT / "docs/implementation/evidence/ENG-014/openapi.json"
    if not schema.exists():
        raise SystemExit(f"{schema} does not exist; run scripts/generate_openapi.py first")
    out = ROOT / "docs/implementation/evidence/ENG-014/api-client.d.ts"
    if "--check" in sys.argv[1:]:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "api-client.d.ts"
            generate(schema, candidate)
            if not out.exists() or candidate.read_text(encoding="utf-8") != out.read_text(encoding="utf-8"):
                raise SystemExit(
                    f"{out.relative_to(ROOT)} is stale relative to {GENERATOR_SPEC}'s output for the "
                    "current OpenAPI schema; run scripts/generate_typescript_client.py and commit the result"
                )
        print(f"{out.relative_to(ROOT)} matches {GENERATOR_SPEC}'s current output")
        return
    generate(schema, out)
    print(f"wrote {out.relative_to(ROOT)} with {GENERATOR_SPEC}")


if __name__ == "__main__":
    main()
