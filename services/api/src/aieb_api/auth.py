"""OIDC-based maintainer identity with server-side roles.

Production MUST fail closed when auth is unconfigured: with no issuer/JWKS
configured, every authenticated route rejects with 401 rather than granting
access. A test identity provider (HS256 shared secret) exists only for
isolated tests and refuses to run unless AIEB_ENV=test.

OIDC (or the test provider) establishes WHO is calling - subject/issuer -
nothing more. It is not consulted for WHAT that caller may do: role
assignments are stored server-side in the `role_bindings` table (spec
section 3/47's "Role assignments are stored server-side") and resolved by
`resolve_roles`/`require_role` from there. A token claiming
`aieb_roles: ["administrator"]` grants nothing on its own; an identity
provider capable of issuing arbitrary claims must not thereby control
authorization.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import jwt
from fastapi import Depends, Request
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .errors import forbidden, unauthenticated
from .models import RoleBinding, User


@dataclass(frozen=True)
class Identity:
    subject: str
    issuer: str


class IdentityProvider(Protocol):
    def verify(self, token: str) -> Identity: ...


class JWKSIdentityProvider:
    """Real OIDC verification: RS256 tokens validated against a provider's JWKS endpoint."""

    def __init__(self, issuer: str, jwks_url: str, audience: str) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_client = PyJWKClient(jwks_url)

    def verify(self, token: str) -> Identity:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=["RS256"], audience=self._audience, issuer=self._issuer,
            )
            subject = claims["sub"]
        except (jwt.PyJWTError, KeyError) as exc:
            raise unauthenticated(f"invalid token: {exc}") from exc
        return Identity(subject=str(subject), issuer=self._issuer)


class TestIdentityProvider:
    """HS256 shared-secret verification for isolated tests only; never wired in production."""

    def __init__(self, shared_secret: str) -> None:
        if os.environ.get("AIEB_ENV") != "test":
            raise RuntimeError("TestIdentityProvider may only be constructed when AIEB_ENV=test")
        self._secret = shared_secret

    def verify(self, token: str) -> Identity:
        try:
            claims = jwt.decode(token, self._secret, algorithms=["HS256"], options={"verify_aud": False})
            subject = claims["sub"]
        except (jwt.PyJWTError, KeyError) as exc:
            raise unauthenticated(f"invalid token: {exc}") from exc
        return Identity(subject=str(subject), issuer=str(claims.get("iss", "test")))


_provider: IdentityProvider | None = None
_configured = False


def configure_from_environment() -> None:
    """Resolve the identity provider from environment variables exactly once.

    Fails closed: if nothing is configured, `_provider` stays None and every
    authenticated request is rejected rather than silently allowed through.
    """
    global _provider, _configured
    _configured = True
    issuer = os.environ.get("AIEB_OIDC_ISSUER")
    jwks_url = os.environ.get("AIEB_OIDC_JWKS_URL")
    audience = os.environ.get("AIEB_OIDC_AUDIENCE")
    if issuer and jwks_url and audience:
        _provider = JWKSIdentityProvider(issuer, jwks_url, audience)
        return
    if os.environ.get("AIEB_ENV") == "test":
        test_secret = os.environ.get("AIEB_TEST_SHARED_SECRET")
        if test_secret:
            _provider = TestIdentityProvider(test_secret)
            return
    _provider = None


def set_provider_for_tests(provider: IdentityProvider) -> None:
    global _provider, _configured
    if os.environ.get("AIEB_ENV") != "test":
        raise RuntimeError("set_provider_for_tests may only run when AIEB_ENV=test")
    _provider = provider
    _configured = True


def _current_provider() -> IdentityProvider | None:
    if not _configured:
        configure_from_environment()
    return _provider


def get_identity(request: Request) -> Identity:
    provider = _current_provider()
    if provider is None:
        raise unauthenticated("authentication is not configured; failing closed")
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise unauthenticated("missing bearer token")
    token = header.removeprefix("Bearer ").strip()
    return provider.verify(token)


def optional_identity(request: Request) -> Identity | None:
    if "authorization" not in request.headers:
        return None
    return get_identity(request)


def resolve_roles(session: Session, identity: Identity) -> tuple[str, ...]:
    """The only source of truth for what an authenticated identity may do.

    An identity with no matching `users` row (never provisioned a role by an
    administrator) resolves to no roles at all - authenticating successfully
    grants no authorization by itself.
    """
    user_id = session.execute(
        select(User.id).where(User.oidc_issuer == identity.issuer, User.oidc_subject == identity.subject)
    ).scalar_one_or_none()
    if user_id is None:
        return ()
    return tuple(session.execute(select(RoleBinding.role).where(RoleBinding.user_id == user_id)).scalars().all())


def require_role(*allowed_roles: str):
    def dependency(identity: Identity = Depends(get_identity), session: Session = Depends(get_session)) -> Identity:
        roles = resolve_roles(session, identity)
        if not set(roles) & set(allowed_roles):
            raise forbidden(f"requires one of roles: {', '.join(allowed_roles)}")
        return identity

    return dependency
