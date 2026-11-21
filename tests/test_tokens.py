import pytest

from ticorom import tokens
from ticorom.db import connect, migrate


@pytest.fixture
def conn(tmp_path):
    migrate(tmp_path / "ticorom.db")
    return connect(tmp_path / "ticorom.db")


def test_a_new_token_is_random_and_only_its_hash_is_stored(conn):
    token = tokens.add(conn, "switch")
    assert tokens.TOKEN_RE.fullmatch(token)
    assert token != tokens.add(conn, "other")
    stored = [row["token_sha256"] for row in conn.execute("SELECT token_sha256 FROM tokens")]
    assert token not in stored and tokens.hash_token(token) in stored


def test_authenticate_finds_active_tokens_only(conn):
    token = tokens.add(conn, "switch")
    assert tokens.authenticate(conn, token).name == "switch"
    assert tokens.authenticate(conn, "0" * 32) is None
    assert tokens.authenticate(conn, "../etc/passwd") is None

    tokens.revoke(conn, "switch")
    assert tokens.authenticate(conn, token) is None


def test_authenticate_records_when_a_token_was_used(conn):
    token = tokens.add(conn, "switch")
    assert tokens.list_tokens(conn)[0].last_used_at is None
    tokens.authenticate(conn, token)
    assert tokens.list_tokens(conn)[0].last_used_at is not None


def test_names_are_unique_among_active_tokens(conn):
    tokens.add(conn, "switch")
    with pytest.raises(tokens.TokenError, match="already exists"):
        tokens.add(conn, "switch")
    tokens.revoke(conn, "switch")
    tokens.add(conn, "switch")  # a revoked name can be used again
    assert [t.revoked_at for t in tokens.list_tokens(conn)] == [None]


@pytest.mark.parametrize("name", ["", "   ", "x" * 65, "bad/name", "-dash"])
def test_bad_names_are_refused(conn, name):
    with pytest.raises(tokens.TokenError, match="token name"):
        tokens.add(conn, name)


def test_a_token_can_be_limited_to_some_consoles(conn):
    token = tokens.add(conn, "kids", ["snes", "gba", "gba"], known_platforms={"gba", "snes", "psx"})
    found = tokens.authenticate(conn, token)
    assert found.platforms == ["gba", "snes"]
    assert found.allows("gba") and not found.allows("psx")


def test_unknown_consoles_are_refused(conn):
    with pytest.raises(tokens.TokenError, match="unknown console names: n64"):
        tokens.add(conn, "kids", ["gba", "n64"], known_platforms={"gba"})


def test_revoking_explains_mistakes(conn):
    with pytest.raises(tokens.TokenError, match="no token named"):
        tokens.revoke(conn, "nobody")
    tokens.add(conn, "switch")
    tokens.revoke(conn, "switch")
    with pytest.raises(tokens.TokenError, match="already revoked"):
        tokens.revoke(conn, "switch")


def test_count_active(conn):
    assert tokens.count_active(conn) == 0
    tokens.add(conn, "a")
    tokens.add(conn, "b")
    tokens.revoke(conn, "a")
    assert tokens.count_active(conn) == 1
