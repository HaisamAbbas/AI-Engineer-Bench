"""Start the AIEB API in local-dev mode with test auth + bootstrap an admin identity.

Sets AIEB_ENV=test so the TestIdentityProvider (HS256) is available, then
seeds a `users` row + `role_bindings` row so GET /v1/me resolves the
`administrator` role. The frontend uses this token for admin reads.

Run:  python scripts/start_local_api.py
Then point apps/web at http://127.0.0.1:8082
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services/api/src"))

os.environ.setdefault("AIEB_ENV", "test")
os.environ.setdefault("AIEB_DATABASE_URL", "postgresql+psycopg://postgres:aieb_test_password@localhost:5544/aieb_test")
os.environ.setdefault("AIEB_TEST_SHARED_SECRET", "dev-shared-secret-DO-NOT-USE-IN-PRODUCTION")
os.environ.setdefault("AIEB_SECRET_KEY", "dev-secret-key-not-for-production")
os.environ.setdefault("AIEB_CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

from aieb_api.db import configure
from aieb_api.models import RoleBinding, User
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

JWT_ISSUER = "test"
JWT_SUBJECT = "dev-admin"
SHARED_SECRET = os.environ["AIEB_TEST_SHARED_SECRET"]


def bootstrap_admin() -> str:
    configure()
    engine = create_engine(os.environ["AIEB_DATABASE_URL"])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        existing = session.execute(
            select(User).where(User.oidc_issuer == JWT_ISSUER, User.oidc_subject == JWT_SUBJECT)
        ).scalar_one_or_none()
        if existing is None:
            user = User(oidc_issuer=JWT_ISSUER, oidc_subject=JWT_SUBJECT, display_name="Dev Admin", id=uuid.uuid4())
            session.add(user)
            session.flush()
            binding = RoleBinding(user_id=user.id, role="administrator", scope="global")
            session.add(binding)
            session.commit()
        else:
            print("[start_local_api] dev admin already exists, reusing")
    import jwt as pyjwt
    token = pyjwt.encode({"iss": JWT_ISSUER, "sub": JWT_SUBJECT}, SHARED_SECRET, algorithm="HS256")
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    (cache / "aieb-dev-token.txt").write_text(token)
    return token


def main() -> None:
    token = bootstrap_admin()
    print("[start_local_api] bootstrapped dev admin; token written to .cache/aieb-dev-token.txt")
    print("[start_local_api] AIEB_DEV_TOKEN=" + token)
    import uvicorn
    uvicorn.run("aieb_api.app:app", host="127.0.0.1", port=8082)


if __name__ == "__main__":
    main()
