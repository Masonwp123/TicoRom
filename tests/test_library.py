import hashlib
import struct
import threading

import pytest

from ticorom.library import CHD_UNKNOWN, HashCancelled, chd_type, sha256_file, stat_file


# A CHD header followed by a chain of metadata entries. No real disc data.
def make_chd(path, tags, version=5):
    header_length = {5: 124, 4: 108, 3: 120}[version]
    header = bytearray(header_length)
    header[:8] = b"MComprHD"
    header[8:12] = struct.pack(">I", header_length)
    header[12:16] = struct.pack(">I", version)
    entries = b""
    offset = header_length
    for index, tag in enumerate(tags):
        next_offset = offset + 16 + 4 if index < len(tags) - 1 else 0
        entries += tag + b"\x01" + (4).to_bytes(3, "big") + struct.pack(">Q", next_offset) + b"data"
        offset += 20
    first = header_length if tags else 0
    where = 48 if version == 5 else 36
    header[where:where + 8] = struct.pack(">Q", first)
    path.write_bytes(bytes(header) + entries)
    return path


def test_sha256_matches_hashlib(tmp_path):
    data = b"x" * (3 * 1024 * 1024 + 17)
    path = tmp_path / "game.bin"
    path.write_bytes(data)
    assert sha256_file(path) == hashlib.sha256(data).hexdigest()


def test_hashing_stops_when_asked(tmp_path):
    path = tmp_path / "game.bin"
    path.write_bytes(b"x" * 2 * 1024 * 1024)
    stop = threading.Event()
    stop.set()
    with pytest.raises(HashCancelled):
        sha256_file(path, stop)


def test_stat_file(tmp_path):
    path = tmp_path / "game.bin"
    path.write_bytes(b"abc")
    size, mtime_ns = stat_file(path)
    assert size == 3 and mtime_ns > 0
    assert stat_file(tmp_path / "missing.bin") is None
    assert stat_file(tmp_path) is None


@pytest.mark.parametrize("tags, expected", [
    ([b"CHT2"], "cd"),
    ([b"CHGD"], "gd"),
    ([b"DVD "], "dvd"),
    # A later entry names the type
    ([b"CIS ", b"CHT2"], "cd"),
    # A hard disk image
    ([b"GDDD"], CHD_UNKNOWN),
    ([], CHD_UNKNOWN),
])
def test_chd_type_from_the_metadata(tmp_path, tags, expected):
    assert chd_type(make_chd(tmp_path / "game.chd", tags)) == expected


@pytest.mark.parametrize("version", [3, 4])
def test_chd_type_of_older_chd_versions(tmp_path, version):
    assert chd_type(make_chd(tmp_path / "game.chd", [b"CHCD"], version=version)) == "cd"


def test_chd_type_of_other_files(tmp_path):
    (tmp_path / "game.iso").write_bytes(b"\x00" * 200)
    assert chd_type(tmp_path / "game.iso") is None
    assert chd_type(tmp_path / "missing.chd") is None
