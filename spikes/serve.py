"""Static file server for the tico spikes.

Serves spikes/site with capability-token paths, HTTP Range, a redirect route,
a route that drops the connection once (for the resume test), and a request
log that records what tico actually sends.
Standard library only. Run make_spike.py first.
"""

from __future__ import annotations

import argparse
import hmac
import json
import re
import sys
import threading
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

HERE = Path(__file__).resolve().parent
SITE = (HERE / "site").resolve()
CONFIG = HERE / "spike_config.json"
LOG = HERE / "serve.log"

# Short, token-free addresses for the index files only, so the token does not
# have to be typed on the Switch. Every download URL inside them uses the token.
REPOSITORIES = {
    "": "round 1 (spikes A, B)",
    "big/": "5,000 packages (spike C)",
    "r2/": "round 2 (spikes D-H)",
    "huge/": "25,000 packages (spike H)",
}
INDEX_ALIASES = {f"/{folder}index.json{gz}": f"{folder}index.json{gz}"
                 for folder in REPOSITORIES for gz in ("", ".gz")}
REDIRECT_ALIAS = "/redirect/index.json"  # 302 -> tokenized index.json

CONTENT_TYPES = {".json": "application/json", ".gz": "application/gzip", ".zip": "application/zip"}
CHUNK = 256 * 1024
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
UNSATISFIABLE = "unsatisfiable"
LOG_LOCK = threading.Lock()

# Files already dropped once by the /cut/ route in this run of the server.
ALREADY_CUT: set[Path] = set()
CUT_LOCK = threading.Lock()


def log(line: str, detail: str = "") -> None:
    stamp = time.strftime("%H:%M:%S")
    with LOG_LOCK:
        print(f"{stamp} {line}", flush=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d')} {stamp} {line}\n{detail}")


def parse_range(header: str, size: int):
    """Return (start, end) inclusive, UNSATISFIABLE, or None to ignore the header."""
    m = RANGE_RE.fullmatch(header.strip())
    if not m or not (m.group(1) or m.group(2)):
        return None  # multi-range or malformed: serve the whole file
    first, last = m.groups()
    if not first:  # suffix form: the last N bytes
        n = int(last)
        return (max(size - n, 0), size - 1) if n and size else UNSATISFIABLE
    start = int(first)
    if start >= size:
        return UNSATISFIABLE
    end = min(int(last), size - 1) if last else size - 1
    return (start, end) if end >= start else None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TicoRomSpike/0.2"
    token = ""
    base_url = ""

    def do_GET(self) -> None:
        self.handle_request(with_body=True)

    def do_HEAD(self) -> None:
        self.handle_request(with_body=False)

    def handle_request(self, with_body: bool) -> None:
        kind, target = self.route(unquote(urlsplit(self.path).path))
        if kind == "redirect":
            # Built from the configured public URL, not the Host header: behind a
            # reverse proxy the Host header may be the upstream address.
            self.send_simple(302, b"", {"Location": f"{self.base_url}{target}"})
        elif kind in ("file", "cut"):
            self.send_file(target, with_body, cut_once=(kind == "cut"))
        else:
            self.send_simple(404, b"not found\n")

    def route(self, path: str):
        """Map a request path to ('file' | 'cut', Path), ('redirect', url path) or (None, None)."""
        token_root = f"/r/{self.token}"
        if path == REDIRECT_ALIAS:
            return "redirect", f"{token_root}/index.json"
        kind = "file"
        rel = INDEX_ALIASES.get(path)
        if rel is None:
            parts = path.split("/", 3)  # ['', 'r', <token>, <rest>]
            if (len(parts) != 4 or parts[1] != "r"
                    or not hmac.compare_digest(parts[2].encode(), self.token.encode())):
                return None, None
            rel = parts[3]
            if rel.startswith("redirect/"):
                return "redirect", f"{token_root}/{quote(rel.removeprefix('redirect/'))}"
            if rel.startswith("cut/"):
                kind, rel = "cut", rel.removeprefix("cut/")
        if "\\" in rel or ":" in rel:
            return None, None
        file = (SITE / rel).resolve()
        if not file.is_relative_to(SITE) or not file.is_file():
            return None, None
        return kind, file

    def send_simple(self, status: int, body: bytes, headers: dict | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.log_exchange(status, (headers or {}).get("Location", ""))

    def send_file(self, file: Path, with_body: bool, cut_once: bool = False) -> None:
        stat = file.stat()
        size = stat.st_size
        etag = f'"{stat.st_mtime_ns:x}-{size:x}"'
        modified = formatdate(stat.st_mtime, usegmt=True)
        headers = {
            "Content-Type": CONTENT_TYPES.get(file.suffix.lower(), "application/octet-stream"),
            "Accept-Ranges": "bytes",
            "ETag": etag,
            "Last-Modified": modified,
        }

        range_header = self.headers.get("Range")
        if_range = self.headers.get("If-Range")
        if range_header and if_range and if_range not in (etag, modified):
            range_header = None  # the file changed since the client's partial copy
        span = parse_range(range_header, size) if range_header else None
        if span == UNSATISFIABLE:
            self.send_simple(416, b"", {"Content-Range": f"bytes */{size}"})
            return
        if span:
            status, (start, end) = 206, span
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        else:
            status, start, end = 200, 0, size - 1
        length = end - start + 1

        # The resume test: the first download of this file that starts at byte 0
        # is dropped a quarter of the way in. Every later request is served in full.
        stop_at = length
        if cut_once and with_body and start == 0 and length > 1:
            with CUT_LOCK:
                if file not in ALREADY_CUT:
                    ALREADY_CUT.add(file)
                    stop_at = length // 4

        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(length))
        self.end_headers()
        self.log_exchange(status, f"{length} bytes")
        if not with_body:
            return

        sent = 0
        began = time.monotonic()
        try:
            with file.open("rb") as f:
                f.seek(start)
                while sent < stop_at:
                    chunk = f.read(min(CHUNK, stop_at - sent))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    sent += len(chunk)
        except ConnectionError:
            self.close_connection = True
        if stop_at < length and sent == stop_at:
            self.close_connection = True
            log(f"    CUT ON PURPOSE {self.shown_path()} after {sent} of {length} bytes; "
                "later requests for it are served in full")
        elif sent < length:
            log(f"    ABORTED {self.shown_path()} after {sent} of {length} bytes")
        elif length >= 1024 * 1024:
            seconds = max(time.monotonic() - began, 1e-6)
            log(f"    done {self.shown_path()} {length} bytes in {seconds:.1f}s "
                f"({length / seconds / 1048576:.1f} MiB/s)")

    def shown_path(self) -> str:
        return self.path.replace(self.token, "<token>")

    def client(self) -> str:
        """The original client when a reverse proxy says who it was, else the peer."""
        headers = getattr(self, "headers", None)
        forwarded = headers.get("X-Forwarded-For") if headers else None
        return forwarded.split(",")[0].strip() if forwarded else self.client_address[0]

    def log_exchange(self, status: int, note: str) -> None:
        extras = [f"{name}={self.headers[name]}" for name in ("Range", "If-Range") if self.headers.get(name)]
        if self.headers.get("Authorization"):
            extras.append("Authorization=present")
        line = (f"{self.client()} {self.command} {self.shown_path()} -> {status} "
                f"{note.replace(self.token, '<token>')} {' '.join(extras)}").rstrip()
        log(line, "".join(f"    {key}: {value}\n" for key, value in self.headers.items()))

    def log_request(self, code="-", size="-") -> None:
        pass  # replaced by log_exchange

    def log_message(self, format: str, *args) -> None:
        log(f"{self.client()} {format % args}")


class Server(ThreadingHTTPServer):
    # On Windows, address reuse would let a second copy bind the same port silently.
    allow_reuse_address = sys.platform != "win32"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bind", default="0.0.0.0", help="address to listen on (default: all interfaces)")
    args = ap.parse_args()

    if not CONFIG.exists() or not (SITE / "index.json").exists():
        sys.exit("Nothing to serve yet. Run:  python make_spike.py")
    config = json.loads(CONFIG.read_text())
    Handler.token = config["token"]
    Handler.base_url = base = config["base_url"]

    try:
        server = Server((args.bind, config["port"]), Handler)
    except OSError as error:
        sys.exit(f"Cannot listen on port {config['port']}: {error}\n"
                 "Pick another with:  python make_spike.py --port <port>")
    print(f"Serving {SITE} on {args.bind}:{config['port']}  (full request log: {LOG.name})")
    print("Repository addresses for tico (Store > Sources > Add repository):")
    for folder, label in REPOSITORIES.items():
        if (SITE / folder / "index.json").exists():
            print(f"  {label:<26} {base}/{folder}index.json")
    print("Other forms of the round 1 address:")
    print(f"  via redirect               {base}/redirect/index.json")
    print(f"  gzipped                    {base}/index.json.gz")
    print(f"  tokenized                  {base}/r/{config['token']}/index.json")
    print("Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
