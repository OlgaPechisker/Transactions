"""Opaque public identifiers: a fixed prefix plus 128 random bits in lowercase base32."""

import base64
import secrets

from bank_transactions.domain.ids import PublicId

__all__ = ["new_public_id"]


def new_public_id(prefix: str) -> PublicId:
    """`<prefix>_` followed by 26 base32 characters (16 random bytes, padding removed)."""
    token = base64.b32encode(secrets.token_bytes(16)).decode("ascii").lower().rstrip("=")
    return PublicId(f"{prefix}_{token}")
