"""Auth fails closed when unconfigured; the test identity provider refuses non-test use.

These checks need no database, unlike tests/test_api_service.py.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

from aieb_api import auth  # noqa: E402


class AuthFailsClosedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {
            key: os.environ.pop(key, None)
            for key in ("AIEB_ENV", "AIEB_OIDC_ISSUER", "AIEB_OIDC_JWKS_URL", "AIEB_OIDC_AUDIENCE", "AIEB_TEST_SHARED_SECRET")
        }
        auth._provider = None  # noqa: SLF001
        auth._configured = False  # noqa: SLF001

    def tearDown(self) -> None:
        for key, value in self._env_backup.items():
            if value is not None:
                os.environ[key] = value
        auth._provider = None  # noqa: SLF001
        auth._configured = False  # noqa: SLF001

    def test_unconfigured_production_fails_closed(self) -> None:
        auth.configure_from_environment()
        with self.assertRaises(Exception) as ctx:
            auth.get_identity(_FakeRequest(authorization=None))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_test_identity_provider_refuses_outside_test_env(self) -> None:
        with self.assertRaises(RuntimeError):
            auth.TestIdentityProvider("secret")

    def test_test_identity_provider_works_when_env_is_test(self) -> None:
        """Verify establishes identity (subject/issuer) only - it must not surface a
        token's aieb_roles claim as authorization; that comes from resolve_roles
        querying role_bindings server-side (see test_api_service.py)."""
        os.environ["AIEB_ENV"] = "test"
        provider = auth.TestIdentityProvider("secret")
        import jwt

        token = jwt.encode({"sub": "u1", "iss": "test", "aieb_roles": ["operator"]}, "secret", algorithm="HS256")
        identity = provider.verify(token)
        self.assertEqual(identity.subject, "u1")
        self.assertFalse(hasattr(identity, "roles"))

    def test_set_provider_for_tests_refuses_outside_test_env(self) -> None:
        with self.assertRaises(RuntimeError):
            auth.set_provider_for_tests(object())  # type: ignore[arg-type]


class _FakeRequest:
    def __init__(self, authorization: str | None) -> None:
        self.headers: dict[str, str] = {} if authorization is None else {"authorization": authorization}


if __name__ == "__main__":
    unittest.main()
