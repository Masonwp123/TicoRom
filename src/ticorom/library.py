from __future__ import annotations

import hashlib
import os
import struct
import threading
import time
from pathlib import Path, PurePosixPath

CHUNK = 1024 * 1024
GIB = 1024 ** 3
FAT32_LIMIT = 4 * GIB  # FAT32 cannot hold a file of this size or larger

# The first metadata tag in a CHD says what kind of disc it holds
CHD_TAG_TYPES = {b"CHT2": "cd", b"CHTR": "cd", b"CHCD": "cd", b"CHGD": "gd", b"DVD ": "dvd"}
CHD_UNKNOWN = "other"  # a CHD whose metadata names none of the above, e.g. a hard disk image


class HashCancelled(Exception):
    """Hashing was stopped because the bridge is shutting down."""


# Whether a path from RomM stays inside the library: relative, and no ".." anywhere.
# RomM is trusted for metadata, but a broken or compromised RomM must not be able
# to make the bridge serve files from elsewhere on the machine.
def is_safe_relative(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and not PurePosixPath(path).is_absolute() and ".." not in parts and "\\" not in path


# (size, mtime in nanoseconds), or None if the path is not a readable file.
def stat_file(path: Path) -> tuple[int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    if not os.path.isfile(path):
        return None
    return info.st_size, info.st_mtime_ns


# SHA-256 of a file, read in chunks. Raises HashCancelled if stop is set midway.
def sha256_file(path: Path, stop: threading.Event | None = None, throttle: Throttle | None = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while chunk := file.read(CHUNK):
            if throttle is not None:
                throttle.wait(len(chunk), stop)
            if stop is not None and stop.is_set():
                raise HashCancelled(str(path))
            digest.update(chunk)
    return digest.hexdigest()


# Caps how fast files are read for hashing, shared by all hashing threads
# (HASH_MAX_MIB_PER_SECOND). Reading slower means less work per second for the
# CPU, the disk and the network; the first sync just takes longer.
class Throttle:
    def __init__(self, bytes_per_second: float) -> None:
        self.bytes_per_second = bytes_per_second
        self._lock = threading.Lock()
        self._next = time.monotonic()

    # Wait until `size` more bytes may be read. Returns at once if `stop` is set.
    def wait(self, size: int, stop: threading.Event | None = None) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(self._next, now)        # no saving up: an idle pause gives no burst later
            self._next = start + size / self.bytes_per_second
        delay = start - now
        if delay > 0:
            if stop is not None:
                stop.wait(delay)
            else:
                time.sleep(delay)


# Run the calling thread at the lowest CPU priority (nice 19), so anything else on
# the machine gets the CPU first. Linux sets this per thread. If the system does not
# allow it, hashing simply runs at normal priority.
def lower_thread_priority() -> None:
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except (OSError, AttributeError):
        pass


# 'cd', 'gd' or 'dvd' for a CHD image, 'other' for an unrecognized one, None if not a CHD.
# Reads only the header and metadata entries, never the compressed data.
def chd_type(path: Path) -> str | None:
    try:
        with open(path, "rb") as file:
            header = file.read(124)
            if len(header) < 56 or header[:8] != b"MComprHD":
                return None
            version = struct.unpack(">I", header[12:16])[0]
            if version >= 5:
                offset = struct.unpack(">Q", header[48:56])[0]
            elif version in (3, 4):
                offset = struct.unpack(">Q", header[36:44])[0]
            else:
                return CHD_UNKNOWN
            # A disc image has a few entries; stop on anything odd
            for _ in range(16):
                if offset == 0:
                    break
                file.seek(offset)
                entry = file.read(16)
                if len(entry) < 16:
                    break
                kind = CHD_TAG_TYPES.get(entry[:4])
                if kind:
                    return kind
                offset = struct.unpack(">Q", entry[8:16])[0]
    except OSError:
        return None
    return CHD_UNKNOWN
