"""Run real browser/API acceptance against an EMPTY disposable PostgreSQL database.

Usage: AIEB_DATABASE_URL=... python scripts/check_admin_browser.py
Requires installed web dependencies, Node 22+, and local Chrome/Edge. No worker
is started: campaigns are staging fixtures, never benchmark executions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if not os.environ.get("AIEB_DATABASE_URL"):
        raise SystemExit("Set AIEB_DATABASE_URL to an empty disposable PostgreSQL database")
    # The identity provider and credentials below exist ONLY in this test process.
    os.environ["AIEB_ENV"] = "test"
    from tests import test_api_service as fixtures
    from aieb_api import db, models
    from sqlalchemy import select, func
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress
    import jwt

    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "services/api", check=True)
    db.configure(os.environ["AIEB_DATABASE_URL"])
    with db.session_factory()() as session:
        if any(session.scalar(select(func.count()).select_from(table)) for table in (models.CampaignRow, models.TaskRevisionRow, models.User)):
            raise SystemExit("Refusing to seed a nonempty database; use a fresh disposable database")
    seed = fixtures.ApiServiceTests()
    seed._seed_task()
    seed._seed_entrant()
    fixtures._grant_roles("browser-operator", ("operator",))
    artifact = ROOT / ".cache/admin-browser"
    artifact.mkdir(parents=True, exist_ok=True)
    registry = seed._registry_payload()
    tokens = {subject: jwt.encode({"sub": subject, "iss": "test", "exp": int(time.time()) + 900},
        os.environ["AIEB_TEST_SHARED_SECRET"], algorithm="HS256") for subject in ("browser-viewer", "browser-operator")}
    fixture_path = artifact / "fixture.json"
    fixture_path.write_text(json.dumps({"draft": seed._draft_body()["draft"], "registry": registry, "tokens": tokens}), encoding="utf-8")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256()))
    (artifact / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (artifact / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    env = dict(os.environ, VITE_API_BASE_URL="http://127.0.0.1:8026", VITE_OIDC_ISSUER="https://127.0.0.1:8446",
        VITE_OIDC_CLIENT_ID="browser-fixture", AIEB_CORS_ALLOWED_ORIGINS="http://127.0.0.1:5176")
    children = []
    try:
        for command, cwd, log in [
            ([sys.executable, "-m", "uvicorn", "aieb_api.app:app", "--host", "127.0.0.1", "--port", "8026"], ROOT, "api.log"),
            (["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "5176", "--strictPort"], ROOT / "apps/web", "web.log"),
        ]:
            with (artifact / log).open("w", encoding="utf-8") as stream:
                children.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT))
        for url in ("http://127.0.0.1:8026/openapi.json", "http://127.0.0.1:5176"):
            for attempt in range(100):
                try:
                    with urllib.request.urlopen(url, timeout=1):
                        break
                except OSError:
                    if any(child.poll() is not None for child in children):
                        raise RuntimeError("Service exited; see .cache/admin-browser/*.log")
                    time.sleep(.1)
            else:
                raise RuntimeError(f"Timed out: {url}")
        subprocess.run(["node", "apps/web/scripts/admin-browser-check.mjs"], cwd=ROOT, env=env, check=True)
        with db.session_factory()() as session:
            campaigns = list(session.scalars(select(models.CampaignRow)))
            assert len(campaigns) == 1, "ambiguous retry created a duplicate campaign"
            assert campaigns[0].state == "running"
            assert session.scalar(select(func.count()).select_from(models.BudgetReservationRow)) == 1
        print("PASS: browser PKCE, server roles, create/reload/retry, edit, preview, freeze, start; one DB campaign/reservation")
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=15)
        for name in ("fixture.json", "key.pem", "cert.pem"):
            (artifact / name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
