import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from ticorom import tokens
from ticorom.app import create_app
from ticorom.db import Database, connect
from ticorom.startup.platforms import parse_platform_map

MAP = parse_platform_map("platforms: {gba: {tico_slug: gba, extensions: [7z]}, ps2: {tico_slug: ps2, extensions: [chd]}}")
DATA = bytes(range(256)) * 4000          # about 1 MB, every byte position recognisable
GIB = 1024 ** 3


class Library:
    def __init__(self, make_settings):
        self.settings = make_settings()
        self.settings.config_path.mkdir()
        self.database = Database.in_config(self.settings.config_path)
        self.database.migrate()
        self.conn = connect(self.database.path)
        self.conn.executescript("""
            INSERT INTO platforms VALUES (1, 'gba', 'gba', 'GBA'), (2, 'ps2', 'ps2', 'PS2');
            INSERT INTO roms (id, platform_id, name, fs_name, romm_updated_at) VALUES
              (7, 1, 'Quest', 'Quest (USA).7z', ''), (8, 2, 'Big', 'Big.chd', ''), (9, 1, 'Later', 'Later.7z', '');
        """)

    # A file in the library, recorded as hashed and published
    def publish(self, rom_id, path, data=None, size=None, tico_slug="gba", status="published"):
        full = self.settings.library_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data or b"")
        if size:
            os.truncate(full, size)      # sparse: large without using disk space
        info = os.stat(full)
        sha = hashlib.sha256(data or b"").hexdigest()
        self.conn.execute("INSERT INTO file_facts (path, size, mtime_ns, sha256) VALUES (?, ?, ?, ?)",
                          (path, info.st_size, info.st_mtime_ns, sha))
        name = path.rsplit("/", 1)[1]
        self.conn.execute("INSERT INTO packages VALUES (?, ?, ?, ?, ?, ?, 'file', ?, ?, NULL)",
                          (f"romm-home-{rom_id}", rom_id, path, tico_slug, f"tico/roms/{tico_slug}", name,
                           sha[:8], status))
        return full, f"romm-home-{rom_id}/{sha[:8]}/{name}"


@pytest.fixture
def library(make_settings):
    return Library(make_settings)


@pytest.fixture
def client(library):
    with TestClient(create_app(library.settings, MAP, library.database)) as client:
        yield client


@pytest.fixture
def token(library):
    return tokens.add(library.conn, "switch")


def test_a_full_download(library, client, token):
    _, address = library.publish(7, "roms/gba/Quest (USA).7z", DATA)
    response = client.get(f"/r/{token}/dl/{address.replace(' ', '%20').replace('(', '%28').replace(')', '%29')}")
    assert response.status_code == 200
    assert response.content == DATA
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-length"] == str(len(DATA))
    assert response.headers["accept-ranges"] == "bytes"


def test_head(library, client, token):
    _, address = library.publish(7, "roms/gba/Quest.7z", DATA)
    response = client.head(f"/r/{token}/dl/{address}")
    assert response.status_code == 200 and response.headers["content-length"] == str(len(DATA))


@pytest.mark.parametrize("header, start, end", [
    ("bytes=100-199", 100, 199),          # the middle
    ("bytes=1000-", 1000, len(DATA) - 1),  # resume from an offset: what tico sends
    ("bytes=-500", len(DATA) - 500, len(DATA) - 1),
])
def test_resume_with_range(library, client, token, header, start, end):
    _, address = library.publish(7, "roms/gba/Quest.7z", DATA)
    response = client.get(f"/r/{token}/dl/{address}", headers={"Range": header})
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes {start}-{end}/{len(DATA)}"
    assert response.content == DATA[start:end + 1]


def test_a_range_past_the_end_is_refused(library, client, token):
    _, address = library.publish(7, "roms/gba/Quest.7z", DATA)
    response = client.get(f"/r/{token}/dl/{address}", headers={"Range": f"bytes={len(DATA)}-"})
    assert response.status_code == 416


def test_resuming_past_4_gib(library, client, token):
    marker = b"beyond four gibibytes"
    full, address = library.publish(8, "roms/ps2/Big.chd", size=5 * GIB, tico_slug="ps2")
    with open(full, "r+b") as file:
        file.seek(4 * GIB + 123)
        file.write(marker)
    info = os.stat(full)
    library.conn.execute("UPDATE file_facts SET mtime_ns = ?", (info.st_mtime_ns,))
    response = client.get(f"/r/{token}/dl/{address}", headers={"Range": f"bytes={4 * GIB + 123}-{4 * GIB + 122 + len(marker)}"})
    assert response.status_code == 206 and response.content == marker
    assert response.headers["content-range"].endswith(f"/{5 * GIB}")


@pytest.mark.parametrize("change", ["token", "version", "filename", "package"])
def test_wrong_addresses_get_404(library, client, token, change):
    _, address = library.publish(7, "roms/gba/Quest.7z", DATA)
    package, version, name = address.split("/")
    parts = {"token": "0" * 32, "version": "00000000", "filename": "Other.7z", "package": "romm-home-99"}
    used = {"token": token, "package": package, "version": version, "filename": name} | {change: parts[change]}
    url = f"/r/{used['token']}/dl/{used['package']}/{used['version']}/{used['filename']}"
    assert client.get(url).status_code == 404


def test_pending_games_cannot_be_downloaded(library, client, token):
    _, address = library.publish(9, "roms/gba/Later.7z", DATA, status="pending")
    assert client.get(f"/r/{token}/dl/{address}").status_code == 404


def test_a_limited_token_cannot_download_other_consoles(library, client):
    limited = tokens.add(library.conn, "kids", ["ps2"], known_platforms={"gba", "ps2"})
    _, address = library.publish(7, "roms/gba/Quest.7z", DATA)
    assert client.get(f"/r/{limited}/dl/{address}").status_code == 404


def test_a_changed_file_is_refused_and_wakes_the_sync(library, client, token):
    full, address = library.publish(7, "roms/gba/Quest.7z", DATA)
    full.write_bytes(DATA + b"more")        # replaced in the library after it was hashed
    assert client.get(f"/r/{token}/dl/{address}").status_code == 404
    assert client.app.state.wake_sync.is_set()


def test_a_missing_file_is_refused(library, client, token):
    full, address = library.publish(7, "roms/gba/Quest.7z", DATA)
    full.unlink()
    assert client.get(f"/r/{token}/dl/{address}").status_code == 404


def test_a_path_outside_the_library_is_never_served(library, client, token):
    outside = library.settings.library_path.parent / "outside.7z"
    outside.write_bytes(b"not a game")
    info = os.stat(outside)
    library.conn.execute("INSERT INTO file_facts (path, size, mtime_ns, sha256) VALUES (?, ?, ?, ?)",
                         ("../outside.7z", info.st_size, info.st_mtime_ns, "ab" * 32))
    library.conn.execute("INSERT INTO packages VALUES ('romm-home-7', 7, '../outside.7z', 'gba', 'tico/roms/gba', "
                         "'outside.7z', 'file', 'abababab', 'published', NULL)")
    assert client.get(f"/r/{token}/dl/romm-home-7/abababab/outside.7z").status_code == 404
