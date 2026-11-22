#!/usr/bin/env bash
# End-to-end trial of TicoRom against the real RomM and library, without touching
# the real config or cache: sync a few platforms into a temporary folder, create a
# token, serve on port 8099, fetch the index, download the largest game and check
# its SHA-256 against the index. Everything is deleted afterwards.
#
# Usage, from the repository root:  .claude/skills/trial-run/trial.sh [fs_slug ...]   (default: gba)
set -euo pipefail

PLATFORMS=("${@:-gba}")
PORT=8099
TRIAL=$(mktemp -d)
SERVER_PID=""

cleanup() {
    if [ -n "$SERVER_PID" ]; then kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; fi
    rm -rf "$TRIAL"
}
trap cleanup EXIT

if ss -ltn | grep -q ":$PORT "; then
    echo "Port $PORT is already in use; stop whatever uses it first." >&2
    exit 1
fi

uv sync -q
export CONFIG_PATH="$TRIAL/config" CACHE_PATH="$TRIAL/cache" SYNC_INTERVAL_MINUTES=600
mkdir -p "$CONFIG_PATH" "$CACHE_PATH"

# A platform map with only the chosen platforms, copied from the shipped default
.venv/bin/python - "$CONFIG_PATH/platforms.yaml" "${PLATFORMS[@]}" <<'EOF'
import sys, yaml
from ticorom.startup.platforms import default_map_text
target, wanted = sys.argv[1], sys.argv[2:]
default = yaml.safe_load(default_map_text())
missing = [p for p in wanted if p not in default["platforms"]]
if missing:
    sys.exit(f"Not in the default map: {', '.join(missing)}. Choose from: {', '.join(sorted(default['platforms']))}")
trial = {"defaults": default.get("defaults", {}), "exclude": [],
         "platforms": {p: default["platforms"][p] for p in wanted}}
open(target, "w").write(yaml.safe_dump(trial))
EOF

echo "== sync (${PLATFORMS[*]})"
if ! .venv/bin/ticorom sync > "$TRIAL/sync.log" 2>&1; then
    grep -E 'FAIL|Error' "$TRIAL/sync.log" || tail -5 "$TRIAL/sync.log"
    echo "The sync failed; stopping." >&2
    exit 1
fi
grep -E 'Sync finished|covers' "$TRIAL/sync.log" || true

echo "== serve on port $PORT"
TOKEN=$(.venv/bin/ticorom token add trial 2>/dev/null | awk '/Token:/{print $2}')
.venv/bin/ticorom serve --port "$PORT" > "$TRIAL/serve.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do curl -fsS -m 2 "http://127.0.0.1:$PORT/v1/health" >/dev/null 2>&1 && break; sleep 0.5; done

echo "== index and download"
curl -fsS -m 30 "http://127.0.0.1:$PORT/r/$TOKEN/index.json" > "$TRIAL/index.json"
.venv/bin/python - "$TRIAL/index.json" "$PORT" "$TRIAL/pick" <<'EOF'
import json, re, sys
packages = json.load(open(sys.argv[1]))["packages"]
if not packages:
    sys.exit("The index has no games.")
game = max(packages, key=lambda p: p["size"])
local = re.sub(r"^https://[^/]+", f"http://127.0.0.1:{sys.argv[2]}", game["url"])
icons = sum("icon" in p for p in packages)
print(f"{len(packages)} games in the index, {icons} with a cover; downloading {game['name']} ({game['size']:,} bytes)")
open(sys.argv[3], "w").write(f"{local}\n{game['sha256']}\n")
EOF
URL=$(sed -n 1p "$TRIAL/pick"); SHA=$(sed -n 2p "$TRIAL/pick")
curl -fsS -m 600 -o "$TRIAL/game" "$URL"
if [ "$(sha256sum "$TRIAL/game" | cut -d' ' -f1)" = "$SHA" ]; then echo "SHA-256 matches the index: yes"; else echo "SHA-256 matches the index: NO"; exit 1; fi

echo "== access log"
if grep -q "$TOKEN" "$TRIAL/serve.log"; then echo "The token appears in the log: NO, it must not"; exit 1; fi
echo "The token is hidden in the log: yes"
