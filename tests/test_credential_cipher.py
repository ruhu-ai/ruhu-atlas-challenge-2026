"""Tests for ``ruhu.tools.cipher``.

Coverage is scoped to the threat model we're defending against:
  - round-trip works with the right AAD
  - wrong AAD / tampered ciphertext raises DecryptionFailed without leaking why
  - key-ring rotation works (decrypt with previous key, re-encrypt with primary)
  - blob self-describes version, key_id, nonce at fixed offsets
  - unknown version and unknown key_id raise the dedicated exceptions

No live-DB dependency; all tests are pure unit tests on the cipher.
"""
from __future__ import annotations

import hashlib

import pytest
from cryptography.fernet import Fernet

from ruhu.tools.cipher import (
    DecryptionFailed,
    FernetCipher,
    UnknownKeyId,
    UnsupportedBlobVersion,
    _HEADER_LEN,
    _KEY_ID_LEN,
    _NONCE_LEN,
    _VERSION_V2,
    build_aad,
)


# ── Test helpers ──────────────────────────────────────────────────────────────


def _new_key() -> str:
    return Fernet.generate_key().decode()


def _aad() -> bytes:
    return build_aad(organization_id="org-1", connection_id="conn-1")


# ── Round-trip ────────────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_decrypt_recovers_plaintext(self) -> None:
        cipher = FernetCipher(primary=_new_key())
        pt = b'{"access_token":"slack-token-fixture","refresh_token":"r"}'
        blob = cipher.encrypt(pt, aad=_aad())
        assert cipher.decrypt(blob, aad=_aad()) == pt

    def test_each_encrypt_uses_a_fresh_nonce(self) -> None:
        # Two encrypts of the same plaintext must not produce the same blob —
        # otherwise an attacker who sees both can prove the plaintext matched
        # without breaking the cipher.
        cipher = FernetCipher(primary=_new_key())
        pt = b"same-plaintext"
        a = cipher.encrypt(pt, aad=_aad())
        b = cipher.encrypt(pt, aad=_aad())
        assert a != b
        assert a[1 + _KEY_ID_LEN:1 + _KEY_ID_LEN + _NONCE_LEN] != b[1 + _KEY_ID_LEN:1 + _KEY_ID_LEN + _NONCE_LEN]


# ── Tamper / AAD defenses ─────────────────────────────────────────────────────


class TestTamperDefense:
    def test_wrong_aad_raises_decryption_failed(self) -> None:
        cipher = FernetCipher(primary=_new_key())
        blob = cipher.encrypt(b"secret", aad=build_aad(organization_id="A", connection_id="c"))
        with pytest.raises(DecryptionFailed):
            cipher.decrypt(blob, aad=build_aad(organization_id="B", connection_id="c"))

    def test_swap_connection_within_same_org_raises(self) -> None:
        # The AAD binds ciphertext to both organization_id AND connection_id,
        # so moving a blob from connection A to connection B within the same
        # org is still rejected.
        cipher = FernetCipher(primary=_new_key())
        blob = cipher.encrypt(b"secret", aad=build_aad(organization_id="org", connection_id="A"))
        with pytest.raises(DecryptionFailed):
            cipher.decrypt(blob, aad=build_aad(organization_id="org", connection_id="B"))

    def test_flipped_ciphertext_byte_raises(self) -> None:
        cipher = FernetCipher(primary=_new_key())
        blob = bytearray(cipher.encrypt(b"secret", aad=_aad()))
        # Flip one bit in the ciphertext region (past the header).
        blob[_HEADER_LEN] ^= 0x01
        with pytest.raises(DecryptionFailed):
            cipher.decrypt(bytes(blob), aad=_aad())

    def test_truncated_blob_raises(self) -> None:
        cipher = FernetCipher(primary=_new_key())
        with pytest.raises(DecryptionFailed):
            cipher.decrypt(b"\x02short", aad=_aad())

    def test_unsupported_version_byte_raises(self) -> None:
        cipher = FernetCipher(primary=_new_key())
        # Build a blob with version 0xFE — must be rejected without attempting decrypt.
        blob = bytes([0xFE]) + b"\x00" * _KEY_ID_LEN + b"\x00" * _NONCE_LEN + b"ct"
        with pytest.raises(UnsupportedBlobVersion):
            cipher.decrypt(blob, aad=_aad())


# ── Key-ring rotation ────────────────────────────────────────────────────────


class TestKeyRotation:
    def test_decrypts_blob_from_previous_key_after_rotation(self) -> None:
        old = _new_key()
        new = _new_key()

        # Encrypt under the old primary.
        before = FernetCipher(primary=old)
        blob = before.encrypt(b"legacy-secret", aad=_aad())

        # Rotate: new primary, old demoted to the ring's previous entries.
        after = FernetCipher(primary=new, previous=[old])
        assert after.decrypt(blob, aad=_aad()) == b"legacy-secret"

    def test_after_rotation_new_writes_use_new_key_id(self) -> None:
        old = _new_key()
        new = _new_key()
        after = FernetCipher(primary=new, previous=[old])

        blob = after.encrypt(b"fresh", aad=_aad())
        new_key_id = hashlib.sha256(__import__("base64").urlsafe_b64decode(new.encode())).digest()[:_KEY_ID_LEN]
        assert blob[1:1 + _KEY_ID_LEN] == new_key_id

    def test_unknown_key_id_raises_dedicated_exception(self) -> None:
        # A blob encrypted with a key that has since been retired AND dropped
        # from `previous` must raise UnknownKeyId, not DecryptionFailed, so the
        # operator can distinguish "key retired too eagerly" from "data tamper".
        retired = _new_key()
        blob = FernetCipher(primary=retired).encrypt(b"x", aad=_aad())

        new = _new_key()
        current = FernetCipher(primary=new)  # retired not in `previous`
        with pytest.raises(UnknownKeyId):
            current.decrypt(blob, aad=_aad())


# ── Blob format (stability contract) ─────────────────────────────────────────


class TestBlobFormat:
    """The on-disk format is a stability contract: future versions must keep
    reading version 0x02 or bump the version byte."""

    def test_first_byte_is_version_v2(self) -> None:
        blob = FernetCipher(primary=_new_key()).encrypt(b"x", aad=_aad())
        assert blob[0] == _VERSION_V2

    def test_key_id_matches_sha256_prefix_of_primary(self) -> None:
        import base64
        key = _new_key()
        cipher = FernetCipher(primary=key)
        blob = cipher.encrypt(b"x", aad=_aad())
        expected = hashlib.sha256(base64.urlsafe_b64decode(key.encode())).digest()[:_KEY_ID_LEN]
        assert blob[1:1 + _KEY_ID_LEN] == expected
        assert cipher.primary_key_id_hex == expected.hex()

    def test_header_length_matches_constants(self) -> None:
        # If the header layout ever changes, tests in other files that parse
        # blobs directly need to follow suit.  Pin it here.
        assert _HEADER_LEN == 1 + _KEY_ID_LEN + _NONCE_LEN


# ── AAD helper ───────────────────────────────────────────────────────────────


class TestBuildAAD:
    def test_determinism(self) -> None:
        a = build_aad(organization_id="o", connection_id="c")
        b = build_aad(organization_id="o", connection_id="c")
        assert a == b

    def test_includes_both_ids(self) -> None:
        a = build_aad(organization_id="alpha", connection_id="beta")
        assert b"alpha" in a and b"beta" in a

    def test_changing_connection_id_changes_aad(self) -> None:
        assert build_aad(organization_id="o", connection_id="c1") != build_aad(
            organization_id="o", connection_id="c2"
        )


# ── from_env ─────────────────────────────────────────────────────────────────


class TestFromEnv:
    def test_raises_when_primary_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RUHU_CREDENTIAL_CIPHER_PRIMARY", raising=False)
        with pytest.raises(ValueError, match="RUHU_CREDENTIAL_CIPHER_PRIMARY"):
            FernetCipher.from_env()

    def test_reads_primary_and_previous(self, monkeypatch: pytest.MonkeyPatch) -> None:
        primary = _new_key()
        older = _new_key()
        oldest = _new_key()
        monkeypatch.setenv("RUHU_CREDENTIAL_CIPHER_PRIMARY", primary)
        monkeypatch.setenv("RUHU_CREDENTIAL_CIPHER_PREVIOUS", f"{older},{oldest}")

        cipher = FernetCipher.from_env()

        # Round-trip with the primary, decrypt older blobs via the ring.
        aad = _aad()
        legacy_a = FernetCipher(primary=older).encrypt(b"a", aad=aad)
        legacy_b = FernetCipher(primary=oldest).encrypt(b"b", aad=aad)
        assert cipher.decrypt(legacy_a, aad=aad) == b"a"
        assert cipher.decrypt(legacy_b, aad=aad) == b"b"


# ── from_env + full rotation cycle (RP-5.7) ───────────────────────────────────


class TestFromEnv:
    def test_builds_ring_from_env_vars(self, monkeypatch) -> None:
        primary, prev_a, prev_b = _new_key(), _new_key(), _new_key()
        monkeypatch.setenv("RUHU_CREDENTIAL_CIPHER_PRIMARY", primary)
        monkeypatch.setenv("RUHU_CREDENTIAL_CIPHER_PREVIOUS", f"{prev_a}, {prev_b}")
        cipher = FernetCipher.from_env()
        # Blobs written under either previous key must decrypt.
        for old_key in (prev_a, prev_b):
            blob = FernetCipher(primary=old_key).encrypt(b"secret", aad=_aad())
            assert cipher.decrypt(blob, aad=_aad()) == b"secret"

    def test_missing_primary_raises_value_error(self, monkeypatch) -> None:
        monkeypatch.delenv("RUHU_CREDENTIAL_CIPHER_PRIMARY", raising=False)
        with pytest.raises(ValueError):
            FernetCipher.from_env()

    def test_blank_previous_entries_are_ignored(self, monkeypatch) -> None:
        monkeypatch.setenv("RUHU_CREDENTIAL_CIPHER_PRIMARY", _new_key())
        monkeypatch.setenv("RUHU_CREDENTIAL_CIPHER_PREVIOUS", " , ,")
        cipher = FernetCipher.from_env()
        blob = cipher.encrypt(b"x", aad=_aad())
        assert cipher.decrypt(blob, aad=_aad()) == b"x"


class TestFullRotationCycle:
    """The documented zero-downtime rotation runbook, end to end.

    1. Rows exist under old_key.
    2. Rotate: primary=new_key, previous=[old_key] — old rows still decrypt.
    3. Re-wrap: decrypt + re-encrypt every row under the new primary.
    4. Retire old_key from the ring — re-wrapped rows fine; any row that was
       missed fails loudly with UnknownKeyId (never silently corrupt).
    """

    def test_rotation_rewrap_and_retirement(self) -> None:
        old_key, new_key = _new_key(), _new_key()
        rows = {
            f"conn-{i}": FernetCipher(primary=old_key).encrypt(
                f"secret-{i}".encode(), aad=build_aad(organization_id="org-1", connection_id=f"conn-{i}")
            )
            for i in range(3)
        }

        # Step 2: rotated ring decrypts everything.
        rotated = FernetCipher(primary=new_key, previous=[old_key])
        for conn_id, blob in rows.items():
            aad = build_aad(organization_id="org-1", connection_id=conn_id)
            assert rotated.decrypt(blob, aad=aad) == f"secret-{conn_id.split('-')[1]}".encode()

        # Step 3: re-wrap all but one row (simulating a missed row).
        rewrapped = {
            conn_id: rotated.encrypt(
                rotated.decrypt(blob, aad=build_aad(organization_id="org-1", connection_id=conn_id)),
                aad=build_aad(organization_id="org-1", connection_id=conn_id),
            )
            for conn_id, blob in rows.items()
            if conn_id != "conn-2"
        }

        # Step 4: retire the old key.
        retired = FernetCipher(primary=new_key)
        for conn_id, blob in rewrapped.items():
            aad = build_aad(organization_id="org-1", connection_id=conn_id)
            assert retired.decrypt(blob, aad=aad).startswith(b"secret-")
        # The missed row fails loudly, pointing at the ring config.
        with pytest.raises(UnknownKeyId):
            retired.decrypt(rows["conn-2"], aad=build_aad(organization_id="org-1", connection_id="conn-2"))
