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


def test_a_throttle_caps_the_hashing_speed(tmp_path):
    from ticorom.library import Throttle
    import time
    data = b"x" * (3 * 1024 * 1024)
    path = tmp_path / "game.bin"
    path.write_bytes(data)
    started = time.monotonic()
    digest = sha256_file(path, throttle=Throttle(6 * 1024 * 1024))     # 6 MiB/s
    # The first MiB goes at once, the other two wait: about 2/6 of a second
    assert time.monotonic() - started >= 0.3
    assert digest == hashlib.sha256(data).hexdigest()


def test_one_throttle_is_shared_by_all_hashing_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from ticorom.library import Throttle
    import time
    paths = []
    for name in ("a.bin", "b.bin"):
        paths.append(tmp_path / name)
        paths[-1].write_bytes(b"y" * (2 * 1024 * 1024))
    throttle = Throttle(8 * 1024 * 1024)                                # 8 MiB/s for both threads together
    started = time.monotonic()
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda p: sha256_file(p, throttle=throttle), paths))
    assert time.monotonic() - started >= 0.35                           # 4 MiB at 8 MiB/s, less the first MiB


def test_stopping_does_not_wait_for_the_throttle(tmp_path):
    from ticorom.library import Throttle
    import time
    path = tmp_path / "game.bin"
    path.write_bytes(b"z" * (4 * 1024 * 1024))
    stop = threading.Event()
    threading.Timer(0.1, stop.set).start()
    started = time.monotonic()
    with pytest.raises(HashCancelled):
        sha256_file(path, stop, Throttle(1024 * 1024))                  # 1 MiB/s: 3 s without the stop
    assert time.monotonic() - started < 1


def test_hashing_threads_run_at_the_lowest_priority():
    import os
    from ticorom.library import lower_thread_priority
    seen = {}

    def worker():
        lower_thread_priority()
        seen["thread"] = os.getpriority(os.PRIO_PROCESS, threading.get_native_id())

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert seen["thread"] == 19
    assert os.getpriority(os.PRIO_PROCESS, threading.get_native_id()) != 19    # only that thread
