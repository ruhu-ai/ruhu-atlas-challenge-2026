from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate RS256 JWT signing material and JWKS output for Ruhu.")
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory to write private/public key files.")
    parser.add_argument("--kid", default=f"jwt-{uuid4().hex[:12]}", help="Active key id to embed in JWT headers and JWKS.")
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

    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": args.kid, "use": "sig", "alg": "RS256"})
    jwks = {"keys": [jwk]}

    private_key_path = out_dir / "jwt-private.pem"
    public_key_path = out_dir / "jwt-public.pem"
    jwks_path = out_dir / "jwks.json"

    private_key_path.write_bytes(private_pem)
    private_key_path.chmod(0o600)
    public_key_path.write_bytes(public_pem)
    jwks_path.write_text(json.dumps(jwks, indent=2) + "\n", encoding="utf-8")

    print(f"Generated signing material in {out_dir}")
    print(f"RUHU_AUTH_JWT_PRIVATE_KEY_PATH={private_key_path}")
    print(f"RUHU_AUTH_JWT_ACTIVE_KID={args.kid}")
    print(f"RUHU_AUTH_JWT_VERIFICATION_JWKS_PATH={jwks_path}")


if __name__ == "__main__":
    main()
