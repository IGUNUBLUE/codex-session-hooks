# Current state of codex-session-hooks

> Last review: 2026-09-18.

## Release

- Latest release: **v1.5.3** — clack-style guided TUI installer.
- Public repo: https://github.com/IGUNUBLUE/codex-session-hooks
- Dev clone: `~/Projects/codex-session-hooks` (location-independent; movable).
- Managed runtime copy: `~/.local/share/codex-session-hooks`
  (`${XDG_DATA_HOME}/codex-session-hooks`), installed via `curl | bash`.
  `~/.codex/hooks.json` points there — repo moves no longer affect the hooks.
- Updating the runtime: re-run the `curl | bash` one-liner or
  `python3 ~/.local/share/codex-session-hooks/install.py`. Running
  `install.py` from the dev clone repoints hooks at the clone (dev mode).

## Installed framework versions (this machine)

- Superpowers plugin `6.3.0` — `openai-curated-remote` marketplace, installed
  and enabled.
- OpenSpec CLI `1.13.1` — `@fission-ai/openspec`, Volta-managed
  (`~/.volta/bin/openspec`).
- Codex hooks feature enabled; hook `herdr` (unrelated) preserved.

## Architecture

- Two `SessionStart` hooks, matcher `^(startup|clear|compact)$`:
  - `superpowers-bootstrap.py` — always emits the active plugin's
    `using-superpowers/SKILL.md` (~800 tokens).
  - `openspec-context.py` — emits project root, discovered `openspec-*`
    skills, CLI-missing warning, and the framework-precedence rule; only
    inside OpenSpec projects, silent otherwise.
- `install.py` — one file, stdlib only: framework updates (official plugin
  mechanism + package-manager detection), atomic hooks.json merge, guided
  TUI (intro/note/outro, arrow-key multiselect, single-keypress confirms,
  spinner), self-update via GitHub releases, prefs at
  `$CODEX_HOME/codex-session-hooks.json`.
- `install.sh` — dual-mode entry: in-place from a clone, or tarball download
  into `CODEX_HOOKS_HOME` when piped.
- Tests: 26 unittests in `tests/test_hooks.py`, all green.

## Composition model (with upstream frameworks)

- OpenSpec owns the persistent record (specs/changes/archive) — the *what*.
- Superpowers owns process discipline (TDD, debugging, review, branch
  mechanics) — the *how*.
- OpenSpec skills de-escalate themselves when not requested; AGENTS.md of the
  host repo can still override everything.
- Full stack requires per-project `openspec init` — exposed explicitly via
  `./install.sh --openspec-init [DIR]`, never automatic from a hook.
