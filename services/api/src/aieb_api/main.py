"""Service entrypoint: `aieb-api` starts uvicorn against AIEB_DATABASE_URL.

No secrets are read from command-line arguments; configuration comes from
environment variables (AIEB_DATABASE_URL, AIEB_OIDC_ISSUER,
AIEB_OIDC_JWKS_URL, AIEB_OIDC_AUDIENCE). See docs/implementation/evidence/ENG-014/.
"""

from __future__ import annotations

import os

import uvicorn


def run() -> None:
    host = os.environ.get("AIEB_API_HOST", "127.0.0.1")
    port = int(os.environ.get("AIEB_API_PORT", "8000"))
    uvicorn.run("aieb_api.app:app", host=host, port=port)


if __name__ == "__main__":
    run()
