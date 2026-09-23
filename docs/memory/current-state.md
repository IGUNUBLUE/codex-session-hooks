# Current state of codex-session-hooks

> Last review: 2026-09-23.

## Release

- Latest release: **v2.0.0** (in progress) — repo-scoped install. Breaking
  change from 1.x: nothing is written to `~/.codex/hooks.json` anymore.
- Public repo: https://github.com/IGUNUBLUE/codex-session-hooks
- Dev clone: `~/Projects/codex-session-hooks` (location-independent).
- No persistent runtime dir since v2: piped `install.sh` downloads to a
  temp dir and installs into the repo resolved from the caller's cwd.
- Re-running the installer in a repo updates hooks + skills in place.

## Installed framework versions (this machine)

- Superpowers skills materialized per-repo from `obra/superpowers` release
  tarballs into `<repo>/.agents/skills/` (manifest-tracked).
- OpenSpec CLI `1.13.1` — `@fission-ai/openspec`, Volta-managed
  (`~/.volta/bin/openspec`). Global tool, per-project init.
- Codex hooks feature enabled globally (`[features] hooks=true` in
  `~/.codex/config.toml` — user capability flag, not a repo artifact).
- Legacy managed hooks in `~/.codex/hooks.json` are offered for cleanup on
  the next installer run; unrelated hooks (`herdr`) preserved.

## Architecture

- Install target = repo root (`--repo`, else `git rev-parse --show-toplevel`
  of cwd, else TUI prompt). Always shown and confirmed before writing.
- Repo layout after install:
  - `<repo>/.codex/hooks.json` — merged SessionStart hooks (atomic + .bak).
  - `<repo>/.codex/hooks/*.py` — copies of the hook scripts (self-contained).
  - `<repo>/.agents/skills/<superpowers-*>` — from upstream tarball; manifest
    `.codex-session-hooks.json` pins the ref and managed dirs.
  - `<repo>/.agents/skills/openspec-*` — via `openspec init --tools codex`.
  - `.gitignore` managed block (default local; committable via TUI choice).
- Hook commands: `"$(git rev-parse --show-toplevel)/.codex/hooks/<script>"`
  (officially recommended git-root form; absolute-path fallback for non-git
  `--repo` targets; `commandWindows` uses the absolute form).
- `superpowers-bootstrap.py` resolves `using-superpowers/SKILL.md` by walking
  ancestors of the script for `.agents/skills`, then `~/.agents/skills`,
  `$CODEX_HOME/skills`; `SUPERPOWERS_USING_SKILL` overrides. No more
  `codex plugin list` in the hot path.
- Tests: unittest suite in `tests/test_hooks.py` (load-by-path).

## Composition model (with upstream frameworks)

- OpenSpec owns the persistent record (specs/changes/archive) — the *what*.
- Superpowers owns process discipline (TDD, debugging, review, branch
  mechanics) — the *how*. Repo-scoped install makes the whole stack
  per-project opt-in.
- Full stack requires per-repo `./install.sh` (hooks + superpowers skills)
  and `--openspec-init` for OpenSpec projects.

## Known upstream caveats

- Project `.codex/hooks.json` loads only when the project layer is trusted;
  hook defs still need `/hooks` approval. First `SessionStart` is skipped
  until then (openai/codex#35306).
- Project hooks are ignored inside git worktrees (openai/codex#27133).
