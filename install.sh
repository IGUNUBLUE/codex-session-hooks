#!/usr/bin/env bash
# Merges this repo's SessionStart hooks into ~/.codex/hooks.json
# without disturbing existing hooks. Idempotent: safe to re-run.
#
# Handlers are matched by script basename, so re-running after moving or
# renaming this repo fixes stale command paths in place.
set -euo pipefail

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
HOOKS_JSON="$CODEX_HOME/hooks.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$CODEX_HOME"
[[ -f "$HOOKS_JSON" ]] || printf '{\n  "hooks": {}\n}\n' > "$HOOKS_JSON"

cp "$HOOKS_JSON" "$HOOKS_JSON.bak.$(date +%Y%m%d%H%M%S)"

SCRIPT_DIR="$SCRIPT_DIR" HOOKS_JSON="$HOOKS_JSON" python3 - <<'PY'
import json, os, sys

path = os.environ["HOOKS_JSON"]
script_dir = os.environ["SCRIPT_DIR"]

# (script basename, statusMessage shown while the hook runs)
HANDLERS = [
    ("session-start.sh", "Loading superpowers"),
    ("openspec-detect.sh", "Loading openspec context"),
]

with open(path) as f:
    try:
        data = json.load(f)
    except json.JSONDecodeError:
        sys.exit(f"error: {path} is not valid JSON — fix it first (backup was saved)")

session_start = data.setdefault("hooks", {}).setdefault("SessionStart", [])
changed = False

for script, status in HANDLERS:
    cmd = f"bash {script_dir}/{script}"
    found = False
    for group in session_start:
        for h in group.get("hooks", []):
            if script in h.get("command", ""):
                found = True
                if h["command"] != cmd:
                    h["command"] = cmd
                    print(f"updated path -> {cmd}")
                    changed = True
    if not found:
        session_start.append({
            "matcher": "startup|resume|clear|compact",
            "hooks": [{
                "type": "command",
                "command": cmd,
                "statusMessage": status,
                "additionalContextLimit": 8000,
            }],
        })
        print(f"added SessionStart hook -> {cmd}")
        changed = True

if changed:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
else:
    print("all hooks already installed — nothing to do")
PY

cat <<'EOF'

Done. Next steps:

  1. Start a new Codex session.
  2. Run /hooks and mark the new hooks as trusted.
     Codex skips untrusted hooks until you approve them.
  3. Make sure hooks are enabled in ~/.codex/config.toml:

       [features]
       hooks = true
EOF
