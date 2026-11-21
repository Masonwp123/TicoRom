from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass

from ticorom.db import transaction

# 128 bits, written as 32 hex characters
TOKEN_BYTES = 16
TOKEN_RE = re.compile(r"[0-9a-f]{32}")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._'-]{0,63}")
# last_used_at is updated at most this often
LAST_USED_EVERY_SECONDS = 60


class TokenError(Exception):
    """A token command cannot be carried out. The message is ready to show."""


@dataclass(frozen=True)
class Token:
    id: int
    name: str
    # Tico console names it may see; None means all
    platforms: list[str] | None
    created_at: int
    revoked_at: int | None
    last_used_at: int | None

    # Whether this token may see a console's games
    def allows(self, tico_slug: str) -> bool:
        return self.platforms is None or tico_slug in self.platforms


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Create a token and return it. This is the only time the token itself exists.
def add(conn: sqlite3.Connection, name: str, platforms: list[str] | None = None,
        known_platforms: set[str] | None = None) -> str:
    name = name.strip()
    if not NAME_RE.fullmatch(name):
        raise TokenError("a token name must be 1-64 characters: letters, digits, spaces, '.', '_', ''' or '-'")
    if platforms is not None:
        platforms = sorted(set(platforms))
        unknown = sorted(set(platforms) - known_platforms) if known_platforms is not None else []
        if not platforms or unknown:
            raise TokenError(f"unknown console names: {', '.join(unknown) or '(none given)'}. "
                             f"Published consoles: {', '.join(sorted(known_platforms or []))}")

    token = secrets.token_hex(TOKEN_BYTES)
    with transaction(conn):
        existing = conn.execute("SELECT revoked_at FROM tokens WHERE name = ?", (name,)).fetchone()
        if existing is not None and existing["revoked_at"] is None:
            raise TokenError(f'a token named "{name}" already exists. Revoke it first, or choose another name.')
        # A revoked token's name can be used again; its old row is replaced
        conn.execute("DELETE FROM tokens WHERE name = ?", (name,))
        conn.execute("INSERT INTO tokens (name, token_sha256, platforms, created_at) VALUES (?, ?, ?, ?)",
                     (name, hash_token(token), json.dumps(platforms) if platforms else None, int(time.time())))
    return token


# Every token, active and revoked, oldest first
def list_tokens(conn: sqlite3.Connection) -> list[Token]:
    return [_token(row) for row in conn.execute("SELECT * FROM tokens ORDER BY created_at, id")]


def count_active(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) FROM tokens WHERE revoked_at IS NULL").fetchone()[0]


# Stop a token from working. Its row is kept so `token list` still shows it.
def revoke(conn: sqlite3.Connection, name: str) -> None:
    with transaction(conn):
        row = conn.execute("SELECT revoked_at FROM tokens WHERE name = ?", (name.strip(),)).fetchone()
        if row is None:
            raise TokenError(f'there is no token named "{name}". See `ticorom token list`.')
        if row["revoked_at"] is not None:
            raise TokenError(f'the token "{name}" is already revoked.')
        conn.execute("UPDATE tokens SET revoked_at = ? WHERE name = ?", (int(time.time()), name.strip()))


# The active token a request carries, or None. Also notes when it was last used.
def authenticate(conn: sqlite3.Connection, token: str) -> Token | None:
    if not TOKEN_RE.fullmatch(token):
        return None
    row = conn.execute("SELECT * FROM tokens WHERE token_sha256 = ? AND revoked_at IS NULL",
                       (hash_token(token),)).fetchone()
    if row is None:
        return None
    now = int(time.time())
    if row["last_used_at"] is None or now - row["last_used_at"] >= LAST_USED_EVERY_SECONDS:
        conn.execute("UPDATE tokens SET last_used_at = ? WHERE id = ?", (now, row["id"]))
    return _token(row)


def _token(row: sqlite3.Row) -> Token:
    return Token(id=row["id"], name=row["name"],
                 platforms=json.loads(row["platforms"]) if row["platforms"] else None,
                 created_at=row["created_at"], revoked_at=row["revoked_at"], last_used_at=row["last_used_at"])
