"""Credential cipher for OAuth tokens and API connection secrets.

Threat model:
  1. DB dump leak  -> attacker cannot read any credential without the master
                      key material.
  2. Cross-tenant ciphertext swap  -> AAD binding to
                      (organization_id, connection_id) means ciphertext moved
                      between tenants or between connections fails to decrypt.
  3. Insider abuse  -> every decrypt emits a ``credential.decrypted`` audit
                      event via ``AuditEventRouter``; see the store layer.

Blob format (self-describing, single column of type ``BYTEA``)::

    +---------+---------+---------+-----------+
    | version | key_id  | nonce   | ciphertext|
    | 1 byte  | 16 byte | 12 byte | N bytes   |
    +---------+---------+---------+-----------+

    version  = 0x02 (AES-256-GCM, key-ring selection by key_id)
    key_id   = sha256(key)[:16]; identifies which key encrypted the row so
               ``FernetCipher`` can pick the right key from the ring on decrypt
    nonce    = random 96-bit AES-GCM nonce
    ciphertext = AES-GCM(plaintext, key, nonce, aad)
               where aad = b"org:<organization_id>|conn:<connection_id>"

Future-proofing: bumping the version byte introduces new formats without a
schema change. Unknown versions raise ``UnsupportedBlobVersion``.

The ``CredentialCipher`` protocol is the only abstraction callers should use.
KMS-envelope and per-row-DEK implementations can plug in later without
touching call sites — see ``docs/operations/credential-encryption.md``.
"""
from __future__ import annotations

import base64
import hashlib
import os
from typing import Protocol, Sequence

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ── Blob layout constants ────────────────────────────────────────────────────

_VERSION_V2 = 0x02
_KEY_ID_LEN = 16
_NONCE_LEN = 12
_HEADER_LEN = 1 + _KEY_ID_LEN + _NONCE_LEN  # version + key_id + nonce


# ── Public exceptions ────────────────────────────────────────────────────────


class AEADCipherError(Exception):
    """Base class for every cipher failure; callers can catch this alone."""


class UnsupportedBlobVersion(AEADCipherError):
    """Ciphertext carries a version byte this cipher does not know how to read."""


class UnknownKeyId(AEADCipherError):
    """The ``key_id`` embedded in the blob matches none of the keys in the ring.

    Usually means the key was retired from the ring before all rows were
    re-wrapped.  Restore the retired key to ``RUHU_CREDENTIAL_CIPHER_PREVIOUS``
    or run ``scripts/rewrap_credentials.py`` to migrate rows.
    """


class DecryptionFailed(AEADCipherError):
    """Ciphertext or AAD was tampered with, or the key is wrong.

    Do not surface the underlying reason in user-facing errors — it helps
    attackers distinguish bad-key from bad-ciphertext probes.
    """


# ── Protocol ─────────────────────────────────────────────────────────────────


class CredentialCipher(Protocol):
    """The only interface callers use.

    Implementations:
      - ``FernetCipher`` (this file) — Fernet-style key ring, all keys held
        in-process.  Right choice for dev and small self-hosted deployments.
      - ``KmsEnvelopeCipher`` (future) — per-row DEK wrapped by a KMS KEK.
        Add when a customer requires customer-managed keys, or when
        compliance asks for row-level cryptoshredding.
    """

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> bytes:
        """Return a versioned, self-describing ciphertext blob."""
        ...

    def decrypt(self, blob: bytes, *, aad: bytes) -> bytes:
        """Return plaintext.  Raises ``DecryptionFailed`` on any tamper;
        ``UnsupportedBlobVersion`` / ``UnknownKeyId`` on structural issues."""
        ...


# ── Fernet-style ring implementation ─────────────────────────────────────────


def _derive_key_id(key_bytes: bytes) -> bytes:
    """First 16 bytes of sha256(key). Deterministic per key, not a secret.

    Included in every ciphertext so decrypt can pick the right ring entry in
    O(1) instead of trying each key in turn.  Leaking key_id does *not* leak
    the key: sha256 is not reversible.
    """
    return hashlib.sha256(key_bytes).digest()[:_KEY_ID_LEN]


def _decode_key(b64_key: str) -> bytes:
    """Accept either raw base64 (44 chars) or url-safe base64 with padding.

    The dev helper ``Fernet.generate_key()`` emits url-safe base64; callers who
    generate keys via ``openssl rand -base64 32`` produce standard base64.
    Both resolve to 32 bytes.
    """
    key_bytes = base64.urlsafe_b64decode(b64_key.encode())
    if len(key_bytes) != 32:
        raise ValueError(
            f"credential cipher key must decode to 32 bytes (AES-256); got {len(key_bytes)}"
        )
    return key_bytes


class FernetCipher:
    """In-process key-ring cipher.

    ``primary`` is used for every ``encrypt()``.  ``previous`` is consulted
    only when ``decrypt()`` sees a blob whose ``key_id`` doesn't match the
    primary key — so rotating a key is zero-downtime:

      1. Generate new_key.  Deploy with
         ``primary = new_key``
         ``previous = [old_primary, ...]``
         All new writes use new_key; old rows still decrypt via old_primary.
      2. Run ``scripts/rewrap_credentials.py`` to re-encrypt under new_key.
      3. Drop old_primary from ``previous``.

    For emergency (key-compromise) rotation, do step 2 first on an expedited
    schedule with the compromised key still in ``previous``, then step 3.
    """

    def __init__(self, *, primary: str, previous: Sequence[str] = ()) -> None:
        primary_bytes = _decode_key(primary)
        previous_bytes = [_decode_key(k) for k in previous]
        # ring: key_id -> AESGCM instance.  Primary is kept as a separate
        # attribute so encrypt() doesn't have to look it up.
        self._primary_bytes = primary_bytes
        self._primary_key_id = _derive_key_id(primary_bytes)
        self._primary = AESGCM(primary_bytes)
        self._ring: dict[bytes, AESGCM] = {self._primary_key_id: self._primary}
        for pk in previous_bytes:
            self._ring[_derive_key_id(pk)] = AESGCM(pk)

    @classmethod
    def from_env(
        cls,
        *,
        primary_var: str = "RUHU_CREDENTIAL_CIPHER_PRIMARY",
        previous_var: str = "RUHU_CREDENTIAL_CIPHER_PREVIOUS",
    ) -> "FernetCipher":
        """Build a cipher from environment variables.  Raises ``ValueError``
        if ``primary_var`` is unset — callers are expected to guard the env
        before calling in staging/production (see ``_enforce_auth_signing_policy``
        style gate in api.py)."""
        primary = os.environ.get(primary_var)
        if not primary:
            raise ValueError(
                f"{primary_var} is not set; cannot build FernetCipher. "
                "Generate a key with "
                "`python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"`"
            )
        previous_raw = os.environ.get(previous_var, "").strip()
        previous = [p for p in (s.strip() for s in previous_raw.split(",")) if p]
        return cls(primary=primary, previous=previous)

    # ── CredentialCipher protocol methods ────────────────────────────────────

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> bytes:
        nonce = os.urandom(_NONCE_LEN)
        ciphertext = self._primary.encrypt(nonce, plaintext, aad)
        return (
            bytes([_VERSION_V2])
            + self._primary_key_id
            + nonce
            + ciphertext
        )

    def decrypt(self, blob: bytes, *, aad: bytes) -> bytes:
        if len(blob) < _HEADER_LEN:
            raise DecryptionFailed("blob shorter than header")
        version = blob[0]
        if version != _VERSION_V2:
            raise UnsupportedBlobVersion(
                f"unsupported credential blob version: 0x{version:02x}"
            )
        key_id = blob[1:1 + _KEY_ID_LEN]
        nonce = blob[1 + _KEY_ID_LEN:_HEADER_LEN]
        ciphertext = blob[_HEADER_LEN:]
        aesgcm = self._ring.get(key_id)
        if aesgcm is None:
            raise UnknownKeyId(
                f"no key in ring matches key_id={key_id.hex()}; "
                "check RUHU_CREDENTIAL_CIPHER_PREVIOUS"
            )
        try:
            return aesgcm.decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            # Do not leak the distinction between "wrong AAD" and "ciphertext
            # tamper" — both indicate the caller should not trust the data.
            raise DecryptionFailed("credential blob failed authentication") from exc

    # ── Helpers (not part of the protocol) ───────────────────────────────────

    @property
    def primary_key_id_hex(self) -> str:
        """Short hex id of the primary key, safe to log.  Useful in audit
        events so operators can correlate a decrypt failure with a specific
        key-ring entry."""
        return self._primary_key_id.hex()


# ── AAD helper ───────────────────────────────────────────────────────────────


def build_aad(*, organization_id: str, connection_id: str) -> bytes:
    """Canonical AAD for credential blobs.

    Bind every ciphertext to both its tenant and the exact row it belongs to.
    Centralised here so every callsite uses the same encoding and nobody
    forgets to include ``organization_id``.  Keep the encoding deterministic
    and ASCII — AAD mismatch is the whole defense against cross-tenant swaps.
    """
    return f"org:{organization_id}|conn:{connection_id}".encode("utf-8")
