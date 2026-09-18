#!/usr/bin/env bash
# Codex SessionStart hook: injects the Superpowers `using-superpowers`
# bootstrap into the model's context.
#
# Codex treats anything a SessionStart hook prints to stdout (exit 0) as
# extra developer context, so we just locate the installed plugin's
# using-superpowers SKILL.md and print it.
#
# Resolution order (first existing file wins):
#   1. $SUPERPOWERS_USING_SKILL  — explicit override, path to SKILL.md
#   2. Newest version under ~/.codex/plugins/cache/*/superpowers/*/...
#   3. ~/.codex/skills/using-superpowers/SKILL.md (manual install)
#
# If nothing is found we still exit 0 so the session starts normally.

set -u

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

if [[ -n "${SUPERPOWERS_USING_SKILL:-}" && -f "$SUPERPOWERS_USING_SKILL" ]]; then
  cat "$SUPERPOWERS_USING_SKILL"
  exit 0
fi

# -Vr sorts version directories newest-first.
newest="$(find "$CODEX_HOME/plugins/cache" \
  -type f -path '*/superpowers/*/skills/using-superpowers/SKILL.md' \
  2>/dev/null | sort -Vr | head -n 1)"

if [[ -n "$newest" ]]; then
  cat "$newest"
  exit 0
fi

if [[ -f "$CODEX_HOME/skills/using-superpowers/SKILL.md" ]]; then
  cat "$CODEX_HOME/skills/using-superpowers/SKILL.md"
fi

exit 0
