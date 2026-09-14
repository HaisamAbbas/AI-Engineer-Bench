"""Regenerate the checked-in OpenAPI schema for services/api (ENG-014).

Typed client generation from this schema is deferred until apps/web exists
(ENG-016); generating a TypeScript client with no consumer would be
premature scaffolding (see DECISIONS.md ENG014-004).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

from aieb_api.app import create_app  # noqa: E402


def main() -> None:
    app = create_app()
    schema = app.openapi()
    out = ROOT / "docs/implementation/evidence/ENG-014/openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} with {len(schema['paths'])} paths")


if __name__ == "__main__":
    main()
