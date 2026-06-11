"""One-shot backfill: encrypt legacy plaintext credential columns.

Phase 2 step 1 of the credential-encryption rollout.  Reads
``api_connections`` rows where the encrypted columns are NULL but the
legacy plaintext columns are populated, and fills in the encrypted columns
using the phase-1 cipher (``FernetCipher.from_env()``).

Design choices (first-principles, not over-engineered):

  * **Chunked SELECT / UPDATE** — scan in batches (default 100 rows) so a
    huge tenant doesn't lock the table or blow memory.
  * **Throttled** — ``--sleep-ms`` between batches (default 50 ms) leaves
    headroom for production traffic.  Set to 0 for maintenance windows.
  * **Idempotent** — the WHERE clause only touches rows where the encrypted
    column is still NULL, so re-running is a no-op on rows already done.
  * **Resumable** — the script walks the table in primary-key order; a
    crash mid-scan resumes from the last successful batch on the next run.
  * **No rewrap worker, no cursor table** — this runs once during the
    phase-2 window, not as an ongoing daemon.

The script is intentionally single-threaded.  Credential encryption is
CPU-bound (AES-GCM) but dominated by DB round-trips; one connection with
small batches beats workers competing for the same table lock.

Usage
-----
    python scripts/backfill_encrypt_credentials.py \\
        --database-url postgresql+psycopg://... \\
        --batch-size 200 \\
        --sleep-ms 25 \\
        --dry-run

Set ``RUHU_CREDENTIAL_CIPHER_PRIMARY`` in the environment; the script
refuses to run without it (no dev-fallback — backfilling under a
throwaway key would need a re-run).

Exit codes
----------
  0  success
  1  invalid arguments / cipher not configured
  2  DB error mid-scan (safe to retry)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Sequence

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

# Script runs standalone; ensure the package is importable when invoked
# directly from the repo root.
from ruhu.db import resolve_database_url
from ruhu.db_models import APIConnectionRecord
from ruhu.tools.cipher import FernetCipher, build_aad

logger = logging.getLogger("backfill_encrypt_credentials")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        required=True,
        help="Target database URL (any SQLAlchemy-accepted form).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Rows to encrypt per transaction (default: 100).",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=50,
        help="Sleep between batches in milliseconds (default: 50).  Set to 0 during maintenance.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be encrypted without writing anything.",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Stop after encrypting this many rows (default: no limit).  Useful for smoke tests.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Emit DEBUG-level logging.",
    )
    return parser.parse_args(argv)


def _encrypt_oauth_token(
    cipher: FernetCipher,
    *,
    organization_id: str,
    connection_id: str,
    oauth_token: dict,
) -> bytes | None:
    if not oauth_token:
        return None
    plaintext = json.dumps(oauth_token, separators=(",", ":"), sort_keys=True).encode()
    return cipher.encrypt(
        plaintext,
        aad=build_aad(organization_id=organization_id, connection_id=connection_id),
    )


def _encrypt_credentials(
    cipher: FernetCipher,
    *,
    organization_id: str,
    connection_id: str,
    credentials_enc: str | None,
) -> bytes | None:
    """Wrap the legacy Fernet-over-dict string in our AEAD envelope.

    Matches ``APIConnectionStore._encrypt_credentials`` so backfilled rows
    look identical to freshly-written rows.
    """
    if credentials_enc is None:
        return None
    return cipher.encrypt(
        credentials_enc.encode("utf-8"),
        aad=build_aad(organization_id=organization_id, connection_id=connection_id),
    )


def _process_batch(
    session: Session,
    cipher: FernetCipher,
    *,
    batch_size: int,
    dry_run: bool,
    after_connection_id: str | None,
) -> tuple[int, str | None]:
    """Encrypt one batch.

    Returns ``(updates_applied, next_cursor)`` where ``next_cursor`` is
    the largest ``connection_id`` seen in this batch (or None if the batch
    was empty).  The cursor is required for dry-run: because dry-run
    doesn't mutate rows, the WHERE clause would otherwise keep returning
    the same rows forever.  The live path is idempotent either way — the
    WHERE clause excludes rows whose ``oauth_token_ct`` is already set —
    but using the cursor keeps the live scan monotonic too, which matters
    for resumability.
    """
    stmt = select(
        APIConnectionRecord.connection_id,
        APIConnectionRecord.organization_id,
        APIConnectionRecord.oauth_token_json,
        APIConnectionRecord.oauth_token_ct,
        APIConnectionRecord.credentials_enc,
        APIConnectionRecord.credentials_ct,
    ).where(
        # Has legacy data + missing ciphertext — the narrow "work to do" set.
        (
            (APIConnectionRecord.oauth_token_ct.is_(None))
            & (APIConnectionRecord.oauth_token_json.isnot(None))
        )
        | (
            (APIConnectionRecord.credentials_ct.is_(None))
            & (APIConnectionRecord.credentials_enc.isnot(None))
        )
    )
    if after_connection_id is not None:
        stmt = stmt.where(APIConnectionRecord.connection_id > after_connection_id)
    stmt = stmt.order_by(APIConnectionRecord.connection_id).limit(batch_size)
    rows = session.execute(stmt).all()

    if not rows:
        return 0, after_connection_id

    updates_applied = 0
    last_seen_id: str | None = after_connection_id
    for row in rows:
        last_seen_id = row.connection_id
        connection_id = row.connection_id
        organization_id = row.organization_id
        new_oauth_ct: bytes | None = None
        new_creds_ct: bytes | None = None

        if row.oauth_token_ct is None and row.oauth_token_json:
            new_oauth_ct = _encrypt_oauth_token(
                cipher,
                organization_id=organization_id,
                connection_id=connection_id,
                oauth_token=dict(row.oauth_token_json or {}),
            )
        if row.credentials_ct is None and row.credentials_enc:
            new_creds_ct = _encrypt_credentials(
                cipher,
                organization_id=organization_id,
                connection_id=connection_id,
                credentials_enc=row.credentials_enc,
            )

        if new_oauth_ct is None and new_creds_ct is None:
            # Row matched the WHERE but both legacy values were empty
            # (e.g. oauth_token_json == {}).  Skip without spending a write.
            continue

        logger.info(
            "encrypt connection_id=%s oauth=%s credentials=%s%s",
            connection_id,
            "yes" if new_oauth_ct is not None else "no",
            "yes" if new_creds_ct is not None else "no",
            " (dry-run)" if dry_run else "",
        )
        if dry_run:
            updates_applied += 1
            continue

        values: dict[str, bytes | None] = {}
        if new_oauth_ct is not None:
            values["oauth_token_ct"] = new_oauth_ct
        if new_creds_ct is not None:
            values["credentials_ct"] = new_creds_ct
        if not values:
            continue
        session.execute(
            update(APIConnectionRecord)
            .where(APIConnectionRecord.connection_id == connection_id)
            .values(**values)
        )
        updates_applied += 1

    if not dry_run:
        session.commit()

    return updates_applied, last_seen_id


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-5s  %(message)s",
    )

    try:
        cipher = FernetCipher.from_env()
    except ValueError as exc:
        logger.error("cipher not configured: %s", exc)
        return 1

    if args.batch_size <= 0:
        logger.error("--batch-size must be > 0")
        return 1

    database_url = resolve_database_url(database_url=args.database_url)
    engine = create_engine(database_url, future=True)

    total = 0
    batches = 0
    cursor: str | None = None
    try:
        while True:
            if args.max_rows is not None and total >= args.max_rows:
                logger.info("reached --max-rows=%d, stopping", args.max_rows)
                break
            with Session(engine) as session:
                n, cursor = _process_batch(
                    session,
                    cipher,
                    batch_size=args.batch_size,
                    dry_run=args.dry_run,
                    after_connection_id=cursor,
                )
            if n == 0:
                logger.info("no more rows to encrypt; done")
                break
            total += n
            batches += 1
            if args.sleep_ms > 0:
                time.sleep(args.sleep_ms / 1000.0)
    except Exception:
        logger.exception("backfill failed mid-scan")
        return 2
    finally:
        engine.dispose()

    verb = "would have encrypted" if args.dry_run else "encrypted"
    logger.info("%s %d rows across %d batches", verb, total, batches)
    return 0


if __name__ == "__main__":
    sys.exit(main())
