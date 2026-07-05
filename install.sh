#!/bin/bash
# immich-tombstone installer.
# Interactive by default; set env vars for unattended installs:
#   IT_DIR          install directory        (default /opt/immich-tombstone)
#   IT_API_URL      Immich URL, e.g. http://127.0.0.1:2283
#   IT_API_KEY      Immich API key
#   IT_MODE         tombstone | archive | delete   (default tombstone)
#   IT_MEDIA_ROOT   path to Immich's UPLOAD_LOCATION on THIS host (tombstone mode)
#   IT_MEDIA_PREFIX path prefix reported by the API (default /data)
#   IT_NTFY_URL     optional ntfy topic URL for notifications
#   IT_CRON_MINUTES cron interval in minutes (default 15)
#   IT_NO_CRON=1    skip crontab setup
set -euo pipefail

say() { printf '%s\n' "$*"; }
die() { say "ERROR: $*" >&2; exit 1; }

command -v python3 >/dev/null || die "python3 is required"

prompt() { # $1 var name, $2 text, $3 default
  # skipped entirely if the variable is already set (even to empty — unattended mode)
  if [ -z "${!1+x}" ]; then
    local val=""
    read -rp "$2 [${3:-}]: " val || true  # EOF (no tty) → fall back to default
    printf -v "$1" '%s' "${val:-${3:-}}"
  fi
}

say "== immich-tombstone installer =="
say "This tool PERMANENTLY destroys the content of re-uploaded files (tombstone mode)."
say "AI-generated code. Use at your own risk. Ctrl-C now if unsure."
say ""

prompt IT_DIR "Install directory" "/opt/immich-tombstone"
prompt IT_API_URL "Immich API URL (e.g. http://127.0.0.1:2283)" ""
[ -n "$IT_API_URL" ] || die "an Immich API URL is required"
prompt IT_API_KEY "Immich API key" ""
[ -n "$IT_API_KEY" ] || die "an API key is required"
prompt IT_MODE "Mode: tombstone (archive + truncate files), archive (no fs access), delete" "tombstone"
case "$IT_MODE" in tombstone|archive|delete) ;; *) die "invalid mode '$IT_MODE'";; esac

if [ "$IT_MODE" = tombstone ]; then
  prompt IT_MEDIA_ROOT "Path of Immich's UPLOAD_LOCATION on this host (must contain library/, thumbs/...)" ""
  [ -d "$IT_MEDIA_ROOT/library" ] || die "'$IT_MEDIA_ROOT/library' not found — is this the right path, and does Immich use the default storage layout?"
  prompt IT_MEDIA_PREFIX "Path prefix the Immich API reports (as mounted in the container)" "/data"
fi
prompt IT_NTFY_URL "ntfy topic URL for notifications (empty to disable)" ""
prompt IT_CRON_MINUTES "Run every N minutes" "15"

# --- validate API access before touching anything ---
say "Checking API access..."
python3 - "$IT_API_URL" "$IT_API_KEY" <<'EOF' || die "cannot reach the Immich API with that URL/key"
import json, sys, urllib.request
url, key = sys.argv[1].rstrip("/"), sys.argv[2]
req = urllib.request.Request(f"{url}/api/server/about", headers={"x-api-key": key})
info = json.load(urllib.request.urlopen(req, timeout=15))
print(f"OK — Immich {info.get('version', 'unknown version')}")
EOF

# --- install ---
mkdir -p "$IT_DIR"
cp "$(dirname "$0")/immich_tombstone.py" "$IT_DIR/"
chmod +x "$IT_DIR/immich_tombstone.py"

python3 - "$IT_DIR/config.json" "$IT_API_URL" "$IT_API_KEY" "$IT_MODE" "${IT_MEDIA_ROOT:-}" "${IT_MEDIA_PREFIX:-/data}" "${IT_NTFY_URL:-}" <<'EOF'
import json, sys
path, url, key, mode, root, prefix, ntfy = sys.argv[1:8]
cfg = {"api_url": url, "api_key": key, "mode": mode}
if mode == "tombstone":
    cfg["media_root"] = root
    cfg["media_prefix"] = prefix
if ntfy:
    cfg["ntfy_url"] = ntfy
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
EOF
chmod 600 "$IT_DIR/config.json"
say "Installed to $IT_DIR"

if [ "${IT_NO_CRON:-}" != 1 ]; then
  MARKER="# immich-tombstone"
  LINE="*/$IT_CRON_MINUTES * * * * /usr/bin/python3 $IT_DIR/immich_tombstone.py >/dev/null 2>&1 $MARKER"
  ( crontab -l 2>/dev/null | grep -vF "$MARKER"; echo "$LINE" ) | crontab -
  say "Cron installed: every $IT_CRON_MINUTES min (marker '$MARKER' in crontab)"
else
  say "Skipping cron (IT_NO_CRON=1). Run manually: python3 $IT_DIR/immich_tombstone.py"
fi

say ""
say "Done. First run will capture the current trash; re-uploads are handled on later runs."
say "Log: $IT_DIR/tombstone.log"
