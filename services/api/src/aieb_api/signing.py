"""Ed25519 signing of publication manifests (ENG-018).

A published snapshot's provenance bundle is signed so a third party can verify,
independently of this service, that a specific (campaign, snapshot_digest,
evidence_manifest_digest, reviewer, timestamp) tuple was approved and published
together. Content digests alone prove internal self-consistency; a signature
adds authenticated provenance.

The signing key is an Ed25519 private key supplied as PEM via
`AIEB_PUBLICATION_SIGNING_KEY`. When unset (local/staging), an ephemeral key is
generated once per process and reused - clearly NOT a durable production key;
production must provide a managed key. The public key and its id travel with
every publication so verification never depends on this service being reachable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SIGNED_MANIFEST_SCHEMA_VERSION = "aieb.publication-signed-manifest/v1"

_lock = threading.Lock()
_ephemeral_private_pem: str | None = None


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    """Same canonical JSON rules as evidence_integrity.evidence_digest, so the
    signed bytes are stable and comparable across processes."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _load_private_key() -> Ed25519PrivateKey:
    global _ephemeral_private_pem
    configured = os.environ.get("AIEB_PUBLICATION_SIGNING_KEY")
    if configured:
        key = serialization.load_pem_private_key(configured.encode("utf-8"), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("AIEB_PUBLICATION_SIGNING_KEY must be an Ed25519 private key in PEM form")
        return key
    with _lock:
        if _ephemeral_private_pem is None:
            _ephemeral_private_pem = generate_signing_key_pem()
        return serialization.load_pem_private_key(_ephemeral_private_pem.encode("utf-8"), password=None)  # type: ignore[return-value]


def generate_signing_key_pem() -> str:
    """A fresh Ed25519 private key in PKCS8 PEM - used for the ephemeral local
    key and by tests that want a deterministic keypair they control."""
    private = Ed25519PrivateKey.generate()
    return private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _public_pem(public: Ed25519PublicKey) -> str:
    return public.public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")


def key_id_for_public_pem(public_pem: str) -> str:
    return hashlib.sha256(public_pem.encode("utf-8")).hexdigest()


class SignedManifest:
    __slots__ = ("signed_manifest", "manifest_signature", "signing_public_key", "signing_key_id")

    def __init__(self, signed_manifest: dict[str, Any], manifest_signature: str, signing_public_key: str, signing_key_id: str) -> None:
        self.signed_manifest = signed_manifest
        self.manifest_signature = manifest_signature
        self.signing_public_key = signing_public_key
        self.signing_key_id = signing_key_id


def sign_manifest(canonical: dict[str, Any]) -> SignedManifest:
    """Sign a canonical publication manifest dict. Returns the signed document,
    the base64 signature, the PEM public key, and its id."""
    private = _load_private_key()
    signature = private.sign(_canonical_bytes(canonical))
    public_pem = _public_pem(private.public_key())
    return SignedManifest(
        signed_manifest=canonical,
        manifest_signature=base64.b64encode(signature).decode("ascii"),
        signing_public_key=public_pem,
        signing_key_id=key_id_for_public_pem(public_pem),
    )


def verify_manifest(signed_manifest: dict[str, Any], manifest_signature: str, signing_public_key: str) -> bool:
    """True iff `manifest_signature` is a valid Ed25519 signature by
    `signing_public_key` over the canonical bytes of `signed_manifest`."""
    try:
        public = serialization.load_pem_public_key(signing_public_key.encode("utf-8"))
        if not isinstance(public, Ed25519PublicKey):
            return False
        public.verify(base64.b64decode(manifest_signature), _canonical_bytes(signed_manifest))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
