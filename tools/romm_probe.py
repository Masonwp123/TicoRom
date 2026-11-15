"""Read-only look at a RomM instance, to check what the bridge will rely on.

Pulls the platform list and every ROM record (with file entries), saves the raw
JSON under probe/, and prints a report: record shapes, file extensions per
platform, whether RomM's paths resolve to files under LIBRARY_PATH, which hashes
exist, and how cover images are reached.

Reads ROMM_URL, ROMM_API_TOKEN and LIBRARY_PATH from the environment or .env.
Standard library only. Usage:  python3 tools/romm_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "probe"
PAGE = 100
GIB = 1024 ** 3


def load_settings() -> dict[str, str]:
    settings = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                settings[key.strip()] = value.strip()
    settings.update({k: v for k, v in os.environ.items() if k in ("ROMM_URL", "ROMM_API_TOKEN", "LIBRARY_PATH")})
    missing = [k for k in ("ROMM_URL", "ROMM_API_TOKEN", "LIBRARY_PATH") if not settings.get(k)]
    if missing:
        sys.exit(f"Missing settings: {', '.join(missing)} (set them in .env)")
    return settings


class RomM:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token

    def request(self, path: str, params: dict | None = None, auth: bool = True) -> tuple[int, bytes, str]:
        """Return (status, body, content type). Never raises on an HTTP error status."""
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        request = urllib.request.Request(url, headers={"User-Agent": "ticorom-probe/0.1"})
        if auth:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers.get("Content-Type", "")

    def json(self, path: str, params: dict | None = None):
        status, body, _ = self.request(path, params)
        if status != 200:
            sys.exit(f"GET {path} -> {status}: {body[:200].decode('utf-8', 'replace')}")
        return json.loads(body)

    def roms(self, platform_id: int) -> list[dict]:
        items, offset = [], 0
        while True:
            page = self.json("/api/roms", {
                "platform_ids": platform_id, "with_files": "true", "limit": PAGE, "offset": offset,
                "with_char_index": "false", "with_filter_values": "false", "with_rom_id_index": "false",
            })
            items += page["items"]
            offset += len(page["items"])
            if not page["items"] or offset >= (page.get("total") or 0):
                return items


def human(size: int) -> str:
    return f"{size / GIB:.1f} GiB" if size >= GIB else f"{size / 1024 ** 2:.0f} MiB"


def main() -> None:
    settings = load_settings()
    romm = RomM(settings["ROMM_URL"], settings["ROMM_API_TOKEN"])
    library = Path(settings["LIBRARY_PATH"])
    OUT.mkdir(exist_ok=True)

    heartbeat = romm.json("/api/heartbeat")
    print(f"RomM {heartbeat.get('SYSTEM', {}).get('VERSION')} at {romm.base}")
    print(f"Library folder: {library}\n")

    platforms = sorted(romm.json("/api/platforms"), key=lambda p: p["fs_slug"])
    (OUT / "platforms.json").write_text(json.dumps(platforms, indent=1))

    all_roms: list[dict] = []
    print(f"{'platform':<14}{'roms':>5}  {'single':>6} {'nested':>6} {'multi':>5}  {'size':>9} {'>=4GiB':>6}  top-level game file extensions")
    for platform in platforms:
        roms = romm.roms(platform["id"])
        (OUT / f"roms-{platform['fs_slug']}.json").write_text(json.dumps(roms, indent=1))
        all_roms += roms
        extensions = Counter()
        big = 0
        for rom in roms:
            for file in rom["files"]:
                if file["file_size_bytes"] >= 4 * GIB:
                    big += 1
                if file.get("is_top_level") and file.get("category") in (None, "game"):
                    extensions[Path(file["file_name"]).suffix.lower() or "(none)"] += 1
        shown = ", ".join(f"{ext} {count}" for ext, count in extensions.most_common(6))
        print(f"{platform['fs_slug']:<14}{len(roms):>5}  "
              f"{sum(r['has_simple_single_file'] for r in roms):>6} "
              f"{sum(r['has_nested_single_file'] for r in roms):>6} "
              f"{sum(r['has_multiple_files'] for r in roms):>5}  "
              f"{human(sum(r['fs_size_bytes'] for r in roms)):>9} {big:>6}  {shown}")

    files = [(rom, file) for rom in all_roms for file in rom["files"]]
    print(f"\nTotal: {len(platforms)} platforms, {len(all_roms)} ROMs, {len(files)} files")

    # --- do RomM's paths resolve under LIBRARY_PATH? ---
    print("\nPath mapping (RomM full_path -> LIBRARY_PATH/full_path):")
    missing, wrong_size, checked = [], [], 0
    for rom, file in files:
        path = library / file["full_path"]
        checked += 1
        try:
            if path.stat().st_size != file["file_size_bytes"]:
                wrong_size.append(file["full_path"])
        except OSError:
            missing.append(file["full_path"])
    print(f"  checked {checked} files: {checked - len(missing) - len(wrong_size)} found with the right size, "
          f"{len(missing)} not found, {len(wrong_size)} with a different size")
    for label, sample in (("not found", missing), ("different size", wrong_size)):
        for name in sample[:5]:
            print(f"    {label}: {name}")
    flagged = [r for r in all_roms if r.get("missing_from_fs") or r.get("is_physical") or not r.get("has_file_on_disk", True)]
    print(f"  ROMs RomM itself marks as missing, physical-only or without a file: {len(flagged)}")
    no_files = [r for r in all_roms if not r["files"]]
    print(f"  ROMs with no file entries: {len(no_files)}")

    # --- record shapes ---
    print("\nRecord shapes:")
    sample = next((r for r in all_roms if r["has_simple_single_file"]), None)
    if sample:
        print("  single file : " + json.dumps({k: sample[k] for k in ("fs_name", "fs_path", "full_path")}))
        print("    its file  : " + json.dumps({k: sample["files"][0][k] for k in ("file_name", "file_path", "full_path", "is_top_level", "category")}))
    sample = next((r for r in all_roms if r["has_multiple_files"]), None)
    if sample:
        print("  multi-file  : " + json.dumps({k: sample[k] for k in ("fs_name", "fs_path", "full_path")}))
        for file in sample["files"][:4]:
            print("    file      : " + json.dumps({k: file[k] for k in ("file_name", "file_path", "is_top_level", "category")}))
    print(f"  file categories: {dict(Counter(str(f.get('category')) for _, f in files))}")
    print(f"  files with an archive listing (archive_members): {sum(bool(f.get('archive_members')) for _, f in files)}")
    print(f"  files with last_modified: {sum(bool(f.get('last_modified')) for _, f in files)} of {len(files)}")
    siblings = sum(bool(r.get("sibling_roms")) for r in all_roms)
    print(f"  ROMs with siblings (other regions or revisions): {siblings}")

    # --- hashes ---
    print("\nHashes RomM already has (the bridge still needs SHA-256):")
    for key in ("crc_hash", "md5_hash", "sha1_hash", "chd_sha1_hash"):
        print(f"  {key:<14} {sum(bool(f.get(key)) for _, f in files)} of {len(files)} files")
    print(f"  any sha256 field on a file: {'sha256_hash' in files[0][1] if files else 'n/a'}")

    # --- metadata the index uses ---
    print("\nMetadata coverage:")
    for key in ("name", "summary", "path_cover_small", "path_cover_large", "url_cover"):
        print(f"  {key:<17} {sum(bool(r.get(key)) for r in all_roms)} of {len(all_roms)} ROMs")

    # --- cover images ---
    print("\nCover images:")
    with_cover = next((r for r in all_roms if r.get("path_cover_small")), None)
    if with_cover:
        cover = with_cover["path_cover_small"]
        print(f"  example path_cover_small: {cover}")
        path = urllib.parse.quote(cover.split("?")[0], safe="/")
        if not path.startswith("/"):
            path = "/" + path
        for label, auth in (("with the token", True), ("without a token", False)):
            status, body, content_type = romm.request(path, auth=auth)
            print(f"  GET {label:<16} -> {status} {content_type} {len(body)} bytes")
    else:
        print("  no ROM has a cover path")

    print(f"\nRaw records saved in {OUT}/ (git-ignored).")


if __name__ == "__main__":
    main()
