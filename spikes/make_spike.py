"""Build the tico spike repositories: payloads plus the index.json files.

Round 1 (spikes A-C) uses dummy data. Round 2 (spikes D-H) also uses the real
games found in site/files. Run this before serve.py, and again after changing
options or files. Standard library only.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import secrets
import socket
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit

HERE = Path(__file__).resolve().parent
SITE = HERE / "site"
FILES = SITE / "files"   # round 1 dummy payloads, and the real games you supply
GEN = SITE / "gen"       # payloads built for round 2
CONFIG = HERE / "spike_config.json"
HASH_CACHE = HERE / "hash_cache.json"
MIB = 1024 * 1024
GIB = 1024 * MIB
ROM_TYPE_CAP = 64 * MIB
FAT32_LIMIT = 4 * GIB    # FAT32 cannot hold a file of this size or larger

# Everything else in site/files is treated as a real game for round 2.
ROUND1_FILES = {"spike1.gba", "spike2.sfc", "Spike 3 (dummy).zip",
                "spike5-small.chd", "spike6-large.chd", "tiny.zip"}

# Which tico console folder each real file's extension belongs to. Edit this if
# your files are for other consoles.
REAL_CONSOLES = {".gba": "gba", ".7z": "snes", ".chd": "ps2"}

# Folder names from tico's "Supported ROM Formats" page.
CONSOLES = [
    "nes", "snes", "n64", "gb", "gbc", "gba", "nds", "3ds", "gc", "wii",
    "triforce", "genesis", "master-system", "game-gear", "sega-cd", "saturn",
    "dc", "psx", "psp", "ps2", "fbneo", "naomi", "atomiswave",
]

ADJECTIVES = [
    "Crimson", "Silent", "Golden", "Hidden", "Frozen", "Burning", "Lost", "Iron",
    "Shadow", "Crystal", "Thunder", "Emerald", "Phantom", "Solar", "Lunar",
    "Savage", "Mystic", "Turbo", "Cosmic", "Ancient",
]
NOUNS = [
    "Odyssey", "Legends", "Racer", "Fortress", "Quest", "Tactics", "Kingdom",
    "Brawlers", "Frontier", "Dungeon", "Circuit", "Chronicle", "Hunters",
    "Empire", "Rally", "Saga", "Strikers", "Voyage", "Arena", "Tales",
]

_hashes: dict[str, str] | None = None


def lan_ip() -> str | None:
    """Address of the interface that carries the default route."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # UDP connect sends nothing
            return s.getsockname()[0]
        except OSError:
            return None


def human(size: int) -> str:
    if size >= GIB:
        return f"{size / GIB:.1f} GiB"
    return f"{size / MIB:.0f} MiB" if size >= MIB else f"{size / 1024:.0f} KiB"


def sha256_of(path: Path) -> str:
    """SHA-256 of a file, cached by (path, size, mtime) like the bridge will."""
    global _hashes
    if _hashes is None:
        _hashes = json.loads(HASH_CACHE.read_text()) if HASH_CACHE.exists() else {}
    stat = path.stat()
    key = f"{path.relative_to(SITE).as_posix()}|{stat.st_size}|{stat.st_mtime_ns}"
    if key not in _hashes:
        if stat.st_size >= 256 * MIB:
            print(f"Hashing {path.name} ({human(stat.st_size)}) ...", flush=True)
        with path.open("rb") as f:
            _hashes[key] = hashlib.file_digest(f, "sha256").hexdigest()
        HASH_CACHE.write_text(json.dumps(_hashes, indent=2))
    return _hashes[key]


def dummy_file(folder: Path, name: str, size: int) -> Path:
    """Deterministic pseudo-random file, so hashes stay the same between runs."""
    path = folder / name
    if path.exists() and path.stat().st_size == size:
        return path
    rng = random.Random(name)
    with path.open("wb") as f:
        left = size
        while left:
            n = min(MIB, left)
            f.write(rng.randbytes(n))
            left -= n
    return path


def build_zip(path: Path, members: list[tuple[str, bytes | Path]], compression: int) -> Path:
    """Write a zip with fixed timestamps, so its hash is stable between runs."""
    with zipfile.ZipFile(path, "w") as z:
        for name, source in members:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = compression
            info.external_attr = 0o644 << 16
            z.writestr(info, source.read_bytes() if isinstance(source, Path) else source)
    return path


def package(ident: str, name: str, description: str, *, payload: Path, url: str, console: str,
            filename: str | None = None, version: str | None = "1", **delivery) -> dict:
    """One index entry. version=None uses the design's rule: first 8 hex of the hash."""
    sha = sha256_of(payload)
    entry = {
        "id": f"ticorom-spike-{ident}",
        "name": name,
        "description": description,
        "category": "game",
        "tags": [console],
        "version": version or sha[:8],
        **delivery,
        "url": url,
    }
    if filename:
        entry["filename"] = filename
    entry.update(install=f"tico/roms/{console}", size=payload.stat().st_size, sha256=sha)
    return entry


def write_index(path: Path, index: dict, indent: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(index, indent=indent).encode()
    path.write_bytes(data)
    path.with_name(path.name + ".gz").write_bytes(gzip.compress(data, mtime=0))


def catalogue(count: int, id_prefix: str, url: str, payload: Path) -> list[dict]:
    """Many near-identical packages for the large-index tests."""
    size, sha = payload.stat().st_size, sha256_of(payload)
    entries = []
    for i in range(count):
        console = CONSOLES[i % len(CONSOLES)]
        title = (f"{ADJECTIVES[i % len(ADJECTIVES)]} "
                 f"{NOUNS[(i // len(ADJECTIVES)) % len(NOUNS)]} "
                 f"{i // (len(ADJECTIVES) * len(NOUNS)) + 1}")
        entries.append({
            "id": f"{id_prefix}-{i:05d}",
            "name": title,
            "tagline": f"Spike C dummy, tag '{console}'",
            "description": (f"Dummy entry {i:05d} of {count} for the large-index test. "
                            "The text is padded to roughly the length a truncated RomM "
                            "summary would have, so the index size is realistic."),
            "category": "game",
            "tags": [console],
            "version": "1",
            "type": "zip",
            "extract": False,
            "url": url,
            "filename": f"Spike C {i:05d} (dummy).zip",
            "install": f"tico/roms/{console}",
            "size": size,
            "sha256": sha,
        })
    return entries


def build_round1(base: str, token: str, file_url, large_mb: int) -> list[dict]:
    """Spikes A and B. Finished on 2026-11-11; kept so the results can be reproduced."""
    rom_gba = dummy_file(FILES, "spike1.gba", 1 * MIB)
    rom_sfc = dummy_file(FILES, "spike2.sfc", 1 * MIB)
    real_zip = build_zip(FILES / "Spike 3 (dummy).zip",
                         [("Spike 3 inner (dummy).gba", random.Random("Spike 3 (dummy).zip").randbytes(MIB))],
                         zipfile.ZIP_STORED)
    small_chd = dummy_file(FILES, "spike5-small.chd", 4 * MIB)
    large = dummy_file(FILES, "spike6-large.chd", large_mb * MIB)

    parts = urlsplit(base)
    userinfo_url = (f"{parts.scheme}://spike:spikepass@{parts.netloc}{parts.path}"
                    f"/r/{token}/files/{quote(rom_gba.name)}")

    bad_sha = package(
        "4-sha-bad", "Spike 4 - wrong sha256 (must fail)",
        "Same as Spike 3 but the sha256 is wrong on purpose. Expect: tico refuses "
        "the download. If it installs, sha256 is not checked for extract:false.",
        payload=real_zip, url=file_url(real_zip), console="gba",
        filename="Spike 4 SHOULD NOT INSTALL.zip", type="zip", extract=False)
    bad_sha["sha256"] = ("0" if bad_sha["sha256"][0] != "0" else "1") + bad_sha["sha256"][1:]

    return [
        package(
            "1-rom-gba", "Spike 1 - rom .gba (baseline)",
            "Baseline: type rom, 1 MiB .gba. Expect: tico/roms/gba/Spike 1 (dummy).gba. "
            "Dummy data, it will not boot.",
            payload=rom_gba, url=file_url(rom_gba), console="gba",
            filename="Spike 1 (dummy).gba", type="rom"),
        package(
            "2-rom-snes", "Spike 2 - rom .sfc",
            "type rom with a .sfc filename. Shows whether type rom is limited to .gba. "
            "If this package is not listed, tico rejected it.",
            payload=rom_sfc, url=file_url(rom_sfc), console="snes",
            filename="Spike 2 (dummy).sfc", type="rom"),
        package(
            "3-keep-zip", "Spike 3 - real zip, extract false (baseline)",
            "Baseline for extract:false with a genuine zip and a percent-encoded URL. "
            "Expect: tico/roms/gba/Spike 3 (dummy).zip saved whole.",
            payload=real_zip, url=file_url(real_zip), console="gba",
            filename="Spike 3 (dummy).zip", type="zip", extract=False),
        bad_sha,
        package(
            "5-a-small", "Spike 5 - SPIKE A small non-zip",
            "type zip + extract:false, but the payload is NOT a zip (4 MiB .chd). "
            "Expect if spike A passes: tico/roms/psx/Spike 5 Small (dummy).chd.",
            payload=small_chd, url=file_url(small_chd), console="psx",
            filename="Spike 5 Small (dummy).chd", type="zip", extract=False),
        package(
            "6-a-large", "Spike 6 - SPIKE A large non-zip",
            f"Same as Spike 5 at {large.stat().st_size // MIB} MiB, over the 64 MiB rom cap. "
            "Pause or interrupt it once and resume, to exercise Range.",
            payload=large, url=file_url(large), console="psx",
            filename="Spike 6 Large (dummy).chd", type="zip", extract=False),
        package(
            "7-b-redirect", "Spike 7 - SPIKE B redirect",
            "The download URL answers 302 to the real file. Expect: "
            "tico/roms/gba/Spike 7 Redirect (dummy).gba.",
            payload=rom_gba, url=file_url(rom_gba, "redirect"), console="gba",
            filename="Spike 7 Redirect (dummy).gba", type="rom"),
        package(
            "8-b-userinfo", "Spike 8 - SPIKE B user:pass in URL (optional)",
            "The URL carries user:password@host. The server log shows whether tico "
            "turns that into an Authorization header.",
            payload=rom_gba, url=userinfo_url, console="gba",
            filename="Spike 8 Userinfo (dummy).gba", type="rom"),
    ]


def find_real_games() -> tuple[dict[str, Path | None], list[str]]:
    """Pick the real files round 2 needs out of site/files."""
    real = sorted((p for p in FILES.iterdir() if p.is_file() and p.name not in ROUND1_FILES),
                  key=lambda p: p.stat().st_size)
    chds = [p for p in real if p.suffix.lower() == ".chd"]
    found = {
        "gba": next((p for p in real if p.suffix.lower() == ".gba"), None),
        "7z": next((p for p in real if p.suffix.lower() == ".7z"), None),
        "chd": next((p for p in reversed(chds) if p.stat().st_size < FAT32_LIMIT), None),
        "chd_over_4g": next((p for p in chds if p.stat().st_size >= FAT32_LIMIT), None),
    }
    unused = [p.name for p in real if p not in found.values()]
    return found, unused


def build_round2(file_url) -> tuple[list[dict], list[str]]:
    """Spikes D-H: resume, real games, extract mode, bundles, size limits."""
    GEN.mkdir(exist_ok=True)
    real, unused = find_real_games()
    notes = [f"not used: {name}" for name in unused]
    file_mode = {"type": "zip", "extract": False, "version": None}
    packages = []

    gba = real["gba"]
    if gba:
        console = REAL_CONSOLES[".gba"]
        packages.append(package(
            "11-real-gba", "Spike 11 - real GBA ROM, file mode",
            f"SPIKE E. A real cartridge ROM delivered as-is (type zip + extract:false). "
            f"Expect: tico/roms/{console}/{gba.name}. Launch it.",
            payload=gba, url=file_url(gba), console=console, filename=gba.name, **file_mode))
    else:
        notes.append("Spikes 11, 13 and 14 left out: no real .gba file in site/files")

    seven = real["7z"]
    if seven:
        console = REAL_CONSOLES[".7z"]
        packages.append(package(
            "12-real-7z", "Spike 12 - real .7z, file mode",
            f"SPIKE E. A real game as a .7z archive, delivered as-is. tico's cartridge "
            f"consoles open .7z directly. Expect: tico/roms/{console}/{seven.name}. Launch it.",
            payload=seven, url=file_url(seven), console=console, filename=seven.name, **file_mode))
    else:
        notes.append("Spike 12 left out: no real .7z file in site/files")

    if gba:
        console = REAL_CONSOLES[".gba"]
        if gba.stat().st_size <= ROM_TYPE_CAP:
            rom_name = f"{gba.stem} (rom type).gba"
            packages.append(package(
                "13-real-rom-type", "Spike 13 - real GBA ROM as type rom (optional)",
                "Round 1's type rom packages failed to install with dummy data. This is the "
                f"same test with a real ROM. Expect if it works: tico/roms/{console}/{rom_name}.",
                payload=gba, url=file_url(gba), console=console, filename=rom_name,
                type="rom", version=None))

        inner = f"{gba.stem} (extract test).gba"
        extract_zip = build_zip(GEN / "Spike 14 Extract.zip", [(inner, gba)], zipfile.ZIP_DEFLATED)
        packages.append(package(
            "14-extract", "Spike 14 - extract mode",
            "SPIKE F. A compressed zip holding one ROM, with no extract flag, so tico unpacks it. "
            f"Expect: tico/roms/{console}/{inner}, and no zip left behind. Launch it.",
            payload=extract_zip, url=file_url(extract_zip), console=console,
            type="zip", version=None))

    for number, label, extra, expect in (
        (15, "Bundle", {},
         "Expect: all three files directly in tico/roms/psx."),
        (16, "Folder Bundle", {"extract_to_folder": True},
         "With extract_to_folder. Expect: the three files in a subfolder of tico/roms/psx. "
         "Note the folder's name, and whether the game shows on the shelf with Scan Subfolders off."),
    ):
        stem = f"Spike {number} {label} (dummy)"
        tracks = [f"{stem} (Track {n}).bin" for n in (1, 2)]
        cue = (f'FILE "{tracks[0]}" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'
               f'FILE "{tracks[1]}" BINARY\r\n  TRACK 02 AUDIO\r\n    INDEX 00 00:00:00\r\n'
               f'    INDEX 01 00:02:00\r\n')
        bundle = build_zip(GEN / f"{stem}.zip", [
            (f"{stem}.cue", cue.encode()),
            (tracks[0], random.Random(tracks[0]).randbytes(8 * MIB)),
            (tracks[1], random.Random(tracks[1]).randbytes(4 * MIB)),
        ], zipfile.ZIP_STORED)
        packages.append(package(
            f"{number}-{label.lower().replace(' ', '-')}", f"Spike {number} - {label.lower()}",
            "SPIKE G. A bridge-built stored zip with a .cue and two .bin files (dummy data, "
            f"will not boot). {expect}",
            payload=bundle, url=file_url(bundle), console="psx", type="zip", version=None, **extra))

    resume = dummy_file(GEN, "spike17-resume.chd", 64 * MIB)
    packages.append(package(
        "17-resume-cut", "Spike 17 - resume after a dropped connection",
        "SPIKE D. The server drops the first download on purpose after 16 of 64 MiB. Watch what "
        "tico does; if it reports an error, start the install again. Dummy data. "
        "Expect: tico/roms/psx/Spike 17 Resume (dummy).chd.",
        payload=resume, url=file_url(resume, "cut"), console="psx",
        filename="Spike 17 Resume (dummy).chd", **file_mode))

    chd = real["chd"]
    if chd:
        console = REAL_CONSOLES[".chd"]
        packages.append(package(
            "18-real-chd", f"Spike 18 - real CHD, {human(chd.stat().st_size)}",
            "SPIKES D, E, H. A real disc image delivered as-is. Partway through, interrupt it once "
            "(sleep the console, or switch Wi-Fi off for 20 seconds) and see whether it resumes. "
            f"Expect: tico/roms/{console}/{chd.name}. Launch it.",
            payload=chd, url=file_url(chd), console=console, filename=chd.name, **file_mode))
    else:
        notes.append("Spike 18 left out: no real .chd under 4 GiB in site/files")

    big = real["chd_over_4g"]
    if big:
        console = REAL_CONSOLES[".chd"]
        packages.append(package(
            "19-chd-over-4g", f"Spike 19 - real CHD over 4 GiB ({human(big.stat().st_size)})",
            "SPIKE H. Larger than a FAT32 card can hold in one file. Expect: fails on FAT32, "
            f"installs on exFAT as tico/roms/{console}/{big.name}. Note when and how it fails.",
            payload=big, url=file_url(big), console=console, filename=big.name, **file_mode))
    else:
        notes.append("Spike 19 left out: no real .chd of 4 GiB or more in site/files")

    return packages, notes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, help="port serve.py listens on (default: keep, else 8765)")
    ap.add_argument("--base-url",
                    help="public base URL. Default: keep the current one, else http://<LAN address>:<port>. "
                         "Pass 'auto' to detect the LAN address again.")
    ap.add_argument("--large-mb", type=int, default=128,
                    help="size of the round 1 dummy large file (default 128)")
    ap.add_argument("--big-count", type=int, default=5000,
                    help="packages in the spike C index (default 5000)")
    ap.add_argument("--huge-count", type=int, default=25000,
                    help="packages in the spike H index (default 25000)")
    args = ap.parse_args()

    FILES.mkdir(parents=True, exist_ok=True)
    config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    token = config.get("token") or secrets.token_hex(16)  # 128 bits
    port = args.port or config.get("port") or 8765

    ip = lan_ip()
    if args.base_url and args.base_url != "auto":
        base, origin = args.base_url, "from --base-url"
    elif config.get("base_url") and args.base_url != "auto":
        base, origin = config["base_url"], "kept from the last run; change with --base-url"
    else:
        base, origin = f"http://{ip or '127.0.0.1'}:{port}", "detected LAN address"
    base = base.rstrip("/")
    root = f"{base}/r/{token}"

    def file_url(payload: Path, route: str = "") -> str:
        """URL of a file under site/. route is 'redirect' or 'cut' for the test routes."""
        rel = quote(payload.relative_to(SITE).as_posix())
        return f"{root}/{route}/{rel}" if route else f"{root}/{rel}"

    round1 = build_round1(base, token, file_url, args.large_mb)
    write_index(SITE / "index.json", {
        "schema": 1,
        "name": "TicoRom Spike",
        "description": "Throwaway test packages for the RomM bridge spikes A and B.",
        "packages": round1,
    }, indent=2)

    tiny_zip = build_zip(FILES / "tiny.zip",
                         [("readme.txt", random.Random("tiny.zip").randbytes(64))], zipfile.ZIP_STORED)
    for folder, count, prefix, title, text in (
        ("big", args.big_count, "ticorom-spike-c", "TicoRom Spike C", "Large-index test."),
        ("huge", args.huge_count, "ticorom-spike-h", "TicoRom Spike H", "Very large index test."),
    ):
        write_index(SITE / folder / "index.json", {
            "schema": 1,
            "name": f"{title} ({count})",
            "description": f"{text} Every package is the same tiny dummy zip.",
            "packages": catalogue(count, prefix, file_url(tiny_zip), tiny_zip),
        }, indent=None)

    round2, notes = build_round2(file_url)
    write_index(SITE / "r2" / "index.json", {
        "schema": 1,
        "name": "TicoRom Spike Round 2",
        "description": "Spikes D-H: resume, real games, extract mode, bundles, size limits.",
        "packages": round2,
    }, indent=2)

    CONFIG.write_text(json.dumps({"token": token, "base_url": base, "port": port}, indent=2))

    def sizes(folder: str) -> str:
        plain = (SITE / folder / "index.json").stat().st_size
        packed = (SITE / folder / "index.json.gz").stat().st_size
        return f"{plain / MIB:.1f} MiB ({packed / 1024:.0f} KiB gzipped)"

    print(f"Wrote {SITE}")
    print(f"  index.json        round 1, {len(round1)} packages")
    print(f"  big/index.json    {args.big_count} packages, {sizes('big')}")
    print(f"  huge/index.json   {args.huge_count} packages, {sizes('huge')}")
    print(f"  r2/index.json     round 2, {len(round2)} packages:")
    for entry in round2:
        print(f"      {entry['name']:<52} {human(entry['size']):>9}")
    for note in notes:
        print(f"  NOTE: {note}")
    print(f"Base URL: {base}  ({origin})")
    if origin == "detected LAN address" and ip is None:
        print(f"  WARNING: no LAN address found; rerun with --base-url http://<this PC's IP>:{port}")
    print("Next: python serve.py")


if __name__ == "__main__":
    main()
