#!/usr/bin/env bash
# Codex SessionStart hook: injects an OpenSpec bootstrap when the current
# project uses OpenSpec.
#
# Detects an openspec/ directory at the git repo root (or cwd) and prints a
# short bootstrap; stdout becomes developer context on exit 0.
# Silent no-op in projects without OpenSpec, so it costs zero tokens there.

set -u

root=""
toplevel="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -n "$toplevel" && -d "$toplevel/openspec" ]]; then
  root="$toplevel"
elif [[ -d "$PWD/openspec" ]]; then
  root="$PWD"
else
  exit 0
fi

active=""
if [[ -d "$root/openspec/changes" ]]; then
  active="$(find "$root/openspec/changes" -mindepth 1 -maxdepth 1 -type d \
    ! -name archive -printf '%f\n' 2>/dev/null | sort | head -20)"
fi

cat <<'EOF'
This project uses OpenSpec (spec-driven development).

- Specs (source of truth): openspec/specs/
- Active changes: openspec/changes/ (each holds proposal.md + tasks.md + spec deltas)
- Archive: openspec/changes/archive/
- Agent skills: .agents/skills/openspec-*/SKILL.md — invoke as $openspec-<id>
  (e.g. $openspec-explore, $openspec-propose, $openspec-apply, $openspec-archive)
- Before changing behavior, check openspec/changes/ for an in-flight change
  covering it, and keep openspec/specs/ in sync when you modify covered behavior.
EOF

if [[ -n "$active" ]]; then
  printf '\nIn-flight changes:\n'
  printf '%s\n' "$active" | sed 's/^/  - /'
fi

exit 0
