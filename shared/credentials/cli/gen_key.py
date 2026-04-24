"""Generate a fresh master key and print to stdout.

Usage:
    python -m shared.credentials.cli.gen_key
        > Save this in MASTER_SECRET env: <base64-key>

Run this ONCE per host (or one shared key across hosts that share secrets).
The output is base64-urlsafe; safe for env var, .env files, GitHub Actions
secrets, etc. Treat it like a password — anyone with this key can decrypt
every credential.
"""
from __future__ import annotations

import sys

from shared.credentials.crypto import generate_master_key


def main(argv: list[str] | None = None) -> int:
    del argv
    key = generate_master_key().decode("ascii")
    print(f"# Save this exact line in your .env (or equivalent secrets manager):")
    print(f"MASTER_SECRET={key}")
    print()
    print("# Verify with:")
    print("# echo \"hello\" | python -c \"")
    print('#   import os, sys')
    print('#   from shared.credentials.crypto import encrypt, decrypt')
    print('#   k = os.environ[\\"MASTER_SECRET\\"]')
    print('#   ct = encrypt(\\"hello\\", k); print(ct)')
    print('#   print(decrypt(ct, k))\"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
