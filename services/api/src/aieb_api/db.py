"""Engine/session wiring. No secrets are hardcoded; DATABASE_URL is read from the environment."""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class DatabaseNotConfigured(RuntimeError):
    """Raised when AIEB_DATABASE_URL is unset; the service must fail closed, not default to sqlite."""


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _database_url() -> str:
    url = os.environ.get("AIEB_DATABASE_URL")
    if not url:
        raise DatabaseNotConfigured("AIEB_DATABASE_URL is not set")
    return url


def configure(database_url: str | None = None, *, echo: bool = False) -> None:
    """(Re)configure the module-level engine. Tests call this explicitly with a real test database."""
    global _engine, _SessionLocal
    url = database_url or _database_url()
    _engine = create_engine(url, echo=echo, pool_pre_ping=True)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def engine():
    if _engine is None:
        configure()
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        configure()
    assert _SessionLocal is not None
    return _SessionLocal


def get_session() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()
