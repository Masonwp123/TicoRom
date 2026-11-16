#!/bin/sh

# Exit immediately if a command exits with a non-zero status
set -e

# Set defaults for PUID, PGID, and paths if not provided
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
CONFIG_PATH="${CONFIG_PATH:-/data/config}"
CACHE_PATH="${CACHE_PATH:-/data/cache}"

# Define your application binary name in one place
APP_BIN="ticorom"

# If run as root, fix permissions and drop privileges
if [ "$(id -u)" = "0" ]; then
    # Ensure config and cache directories exist and match PUID/PGID
    for dir in "$CONFIG_PATH" "$CACHE_PATH"; do
        mkdir -p "$dir"
        chown "$PUID:$PGID" "$dir"
    done

    # Drop privileges and execute the application
    exec setpriv --reuid "$PUID" --regid "$PGID" --clear-groups "$APP_BIN" "$@"
else
  # If not running as root, just ensure the directories exist
  for dir in "$CONFIG_PATH" "$CACHE_PATH"; do
        mkdir -p "$dir"
  done

  # Execute the application under the existing container user
  exec "$APP_BIN" "$@"
fi
