from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a new RS256 signing keypair and merge it into a rotated JWKS bundle."
    )
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory to write the rotated key files.")
    parser.add_argument(
        "--existing-jwks",
        type=Path,
        required=True,
        help="Existing JWKS JSON file containing the public keys that must remain valid during rotation.",
    )
    parser.add_argument("--kid", default=f"jwt-{uuid4().hex[:12]}", help="Active key id for the new signing key.")
    parser.add_argument("--key-size", type=int, default=2048, help="RSA key size in bits. Default: 2048.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=args.key_size)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    new_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    new_jwk.update({"kid": args.kid, "use": "sig", "alg": "RS256"})

    existing_payload = json.loads(args.existing_jwks.read_text(encoding="utf-8"))
    existing_keys = existing_payload.get("keys", [])
    if not isinstance(existing_keys, list):
        raise SystemExit("--existing-jwks must contain a JSON object with a keys array")
    merged_keys = [
        key
        for key in existing_keys
        if isinstance(key, dict) and key.get("kid") != args.kid
    ]
    merged_keys.append(new_jwk)
    merged_jwks = {"keys": merged_keys}

    private_key_path = out_dir / "jwt-private.pem"
    public_key_path = out_dir / "jwt-public.pem"
    jwks_path = out_dir / "jwks.json"

    private_key_path.write_bytes(private_pem)
    private_key_path.chmod(0o600)
    public_key_path.write_bytes(public_pem)
    jwks_path.write_text(json.dumps(merged_jwks, indent=2) + "\n", encoding="utf-8")

    print(f"Generated rotated signing material in {out_dir}")
    print(f"RUHU_AUTH_JWT_PRIVATE_KEY_PATH={private_key_path}")
    print(f"RUHU_AUTH_JWT_ACTIVE_KID={args.kid}")
    print(f"RUHU_AUTH_JWT_VERIFICATION_JWKS_PATH={jwks_path}")
    print("Keep the previous public keys in JWKS until the maximum token lifetime has elapsed.")


if __name__ == "__main__":
    main()
