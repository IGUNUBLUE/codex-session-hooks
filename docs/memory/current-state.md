# Current state of codex-session-hooks

> Last review: 2026-09-23.

## Release

- Latest release: **v2.1.0** (published) — multi-harness
  (`--harness codex,opencode,omp`), shared handlers under
  `.agents/session-hooks/`, native TS adapters for OpenCode and oh-my-pi.
- v2.0.0 was the repo-scoped break: nothing is written to
  `~/.codex/hooks.json` anymore.
- Public repo: https://github.com/IGUNUBLUE/codex-session-hooks
- Dev clone: `~/Projects/codex-session-hooks` (location-independent).
- No persistent runtime dir since v2: piped `install.sh` downloads to a
  temp dir and installs into the repo resolved from the caller's cwd.
- Re-running the installer in a repo updates hooks + skills in place.

## Installed framework versions (this machine)

- Superpowers skills materialized per-repo from `obra/superpowers` release
  tarballs into `<repo>/.agents/skills/` (manifest-tracked); tested v6.4.1.
- OpenSpec CLI `1.13.2` — `@fission-ai/openspec`, Volta-managed
  (`~/.volta/bin/openspec`). Global tool, per-project init.
- Harness CLIs installed: Codex (hooks enabled globally via
  `[features] hooks=true` in `~/.codex/config.toml`), OpenCode `v2.0.15`,
  oh-my-pi `omp/18.1.21`.
- `~/.codex/hooks.json` contains only the unrelated `herdr` hook; the v1
  managed-runtime dir `~/.local/share/codex-session-hooks/` was deleted.

## Architecture

- Install target = repo root (`--repo`, else `git rev-parse --show-toplevel`
  of cwd, else TUI prompt). Always shown and confirmed before writing.
- `--harness` selects destinations: `codex`, `opencode`, `omp`, `all`,
  `none`. Default: CLIs detected via `shutil.which` (TUI multiselect /
  `-y` accepts all; falls back to `codex` when nothing is detected).
  Deselecting a harness removes its managed adapter.
- Repo layout after install:
  - `<repo>/.agents/session-hooks/*.py` — shared handlers (single source of
    context logic for all harnesses). v2.0's `.codex/hooks/` copies are
    migrated/cleaned by the installer.
  - `<repo>/.codex/hooks.json` — merged SessionStart hooks (atomic + .bak).
  - `<repo>/.opencode/plugins/session-hooks.ts` — plain `{id, setup}`
    object (NOT `@opencode/plugin`, which does not resolve in
    auto-discovered plugins). Runs handlers once at setup, pushes stdout
    into `event.system` on every `context` hook call.
  - `<repo>/.omp/extensions/session-hooks.ts` — lazy-cached handler output
    injected as a deduplicated `custom` message in the `context` event.
  - `<repo>/.agents/skills/<superpowers-*>` — from upstream tarball; manifest
    `.codex-session-hooks.json` pins the ref and managed dirs.
  - OpenSpec per harness: `openspec init --tools codex,opencode,oh-my-pi`
    → `.agents/skills/openspec-*`, `.opencode/commands/opsx-*`,
    `.omp/commands/opsx-*`.
  - `.gitignore` managed block — handlers, skills, manifest, and
    `.codex/hooks.json` (codex only). **Adapters are never ignored**: omp
    extension discovery honors `.gitignore` (skill discovery does not).
- Codex hook commands: `"$(git rev-parse --show-toplevel)/.agents/
  session-hooks/<script>"` (absolute-path fallback for non-git `--repo`
  targets; `commandWindows` uses the absolute form).
- `openspec-context.py` reports `opsx-*` commands from
  `.opencode/commands` + `.omp/commands` in addition to `openspec-*` skills.
- Tests: unittest suite in `tests/test_hooks.py` (load-by-path), 52 tests.

## Composition model (with upstream frameworks)

- OpenSpec owns the persistent record (specs/changes/archive) — the *what*.
- Superpowers owns process discipline (TDD, debugging, review, branch
  mechanics) — the *how*. Repo-scoped install makes the whole stack
  per-project opt-in.
- Full stack requires per-repo `./install.sh` (hooks + superpowers skills)
  and `--openspec-init` for OpenSpec projects.
- Verified end-to-end on this machine: OpenCode v2.0.15 model confirmed the
  injected `<superpowers-bootstrap>` block; omp 18.1.21 returned the probe
  answer both via `--extension` and via native `.omp/extensions` discovery.

## Known upstream caveats

- Project `.codex/hooks.json` loads only when the project layer is trusted;
  hook defs still need `/hooks` approval. First `SessionStart` is skipped
  until then (openai/codex#35306).
- Project hooks are ignored inside git worktrees (openai/codex#27133).
- omp native extension discovery is cwd-only (`<cwd>/.omp/extensions`) and
  gitignore-aware — it does not walk ancestors; run `omp` from the repo root.
- OpenCode project plugins must not import `@opencode/plugin` (no package
  resolution in `.opencode/plugins/`); export `{id, setup}` instead.
