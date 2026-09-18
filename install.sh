#!/usr/bin/env bash
# Merges the superpowers SessionStart hook into ~/.codex/hooks.json
# without disturbing existing hooks. Idempotent: safe to re-run.
set -euo pipefail

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
HOOKS_JSON="$CODEX_HOME/hooks.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_CMD="bash $SCRIPT_DIR/session-start.sh"

mkdir -p "$CODEX_HOME"
[[ -f "$HOOKS_JSON" ]] || printf '{\n  "hooks": {}\n}\n' > "$HOOKS_JSON"

cp "$HOOKS_JSON" "$HOOKS_JSON.bak.$(date +%Y%m%d%H%M%S)"

HOOK_CMD="$HOOK_CMD" HOOKS_JSON="$HOOKS_JSON" python3 - <<'PY'
import json, os, sys

path = os.environ["HOOKS_JSON"]
cmd = os.environ["HOOK_CMD"]

with open(path) as f:
    try:
        data = json.load(f)
    except json.JSONDecodeError:
        sys.exit(f"error: {path} is not valid JSON — fix it first (backup was saved)")

session_start = data.setdefault("hooks", {}).setdefault("SessionStart", [])

def already_installed(group):
    return any(h.get("command") == cmd for h in group.get("hooks", []))

if any(already_installed(g) for g in session_start):
    print("already installed — nothing to do")
else:
    session_start.append({
        "matcher": "startup|resume|clear|compact",
        "hooks": [{
            "type": "command",
            "command": cmd,
            "statusMessage": "Loading superpowers",
            "additionalContextLimit": 8000,
        }],
    })
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"added SessionStart hook -> {cmd}")
PY

cat <<'EOF'

Done. Next steps:

  1. Start a new Codex session.
  2. Run /hooks and mark the "Loading superpowers" hook as trusted.
     Codex skips untrusted hooks until you approve them.
  3. Make sure hooks are enabled in ~/.codex/config.toml:

       [features]
       hooks = true
EOF
