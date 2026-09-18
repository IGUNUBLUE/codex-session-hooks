# AGENTS.md

## Project overview

`codex-session-hooks` installs and manages Codex CLI `SessionStart` hooks that
inject framework context into every session, plus an installer that keeps the
upstream frameworks current. It does **not** fork or vendor either framework.

Two managed hooks, matched on `^(startup|clear|compact)$` (resume is skipped
on purpose — the resumed transcript already carries the context):

| Hook | Script | Emits |
|---|---|---|
| `superpowers` | `superpowers-bootstrap.py` | The active Superpowers `using-superpowers/SKILL.md`, resolved from `codex plugin list --json` — never from path sorting or disabled caches |
| `openspec` | `openspec-context.py` | Nearest ancestor `openspec/config.yaml` context: discovered `openspec-*` skills, CLI-missing warning, and the framework-precedence rule. Silent outside OpenSpec projects |

Everything is stdlib-only Python 3.10+ (`from __future__ import annotations`)
and POSIX shell. There are no third-party dependencies and none should be
added — the installer must stay runnable via `curl | bash` on a bare machine.

## Repository layout

- `install.py` — installer: framework updates, hooks.json merge, guided TUI,
  self-update. All logic lives here; keep it dependency-free.
- `install.sh` — entry point. Dual mode: runs `install.py` in place from a
  clone, or downloads the repo tarball into a persistent directory when piped.
- `superpowers-bootstrap.py`, `openspec-context.py` — SessionStart handlers.
  Must stay fast (hook timeout is 15s) and print nothing when inactive.
- `session-start.sh`, `openspec-detect.sh` — legacy handlers kept only so the
  installer can recognize and migrate old installs (see `legacy_scripts`).
- `hooks.json` — reference shape only; never edited by hand for real installs.
- `tests/test_hooks.py` — unittest suite; loads modules by path.
- `docs/memory/` — project memory; see "Memory system" below.

## Setup and verification

Prerequisites the installer verifies but does not install: Codex CLI,
Python ≥3.10, Node.js ≥20.19.0, and one JS package manager
(npm → pnpm → bun → Yarn 1 → volta, in preference order for fresh installs;
an existing Volta-owned OpenSpec stays on Volta).

```bash
# Full verification suite — run before every commit
python3 -m unittest discover -s tests -v
python3 -m py_compile install.py superpowers-bootstrap.py openspec-context.py
bash -n install.sh session-start.sh openspec-detect.sh
python3 -m json.tool hooks.json >/dev/null

# Install / update (from a clone)
./install.sh                 # guided TUI when a terminal exists
./install.sh -y              # non-interactive
./install.sh --hooks openspec --skip-framework-updates
./install.sh --openspec-init [DIR]   # explicit per-project OpenSpec setup

# Standalone (managed runtime dir, survives repo moves)
curl -fsSL https://raw.githubusercontent.com/IGUNUBLUE/codex-session-hooks/main/install.sh | bash
```

Tests: `python3 -m unittest discover -s tests`. Add or update tests for any
code you change, even if nobody asked. Fakes for prompts should expose a real
`fileno()` only when testing the raw-key paths — `io.StringIO` subclasses fall
back to line mode, which is what most prompt tests want.

## Invariants — do not break

- **Location-independent.** Scripts resolve everything from
  `Path(__file__).resolve().parent`. No absolute paths may be hardcoded.
- **Ownership by marker.** Managed hooks are identified by the
  `--managed-by=codex-session-hooks` token in the command — never by script
  basename alone. Unrelated hooks must be preserved byte-for-byte.
- **Atomic writes.** `hooks.json` is replaced via temp-file + `os.replace`
  with a timestamped `.bak` backup. No partial writes.
- **Absolute paths in hook commands.** SessionStart runs in whatever directory
  the user opened Codex in — there is no stable base for a relative path.
- **Hooks never write.** SessionStart handlers only print context. They must
  not create files in arbitrary repos — `openspec init` runs only through the
  explicit `--openspec-init` flag, never from a hook.
- **Conditional context.** `openspec-context.py` emits nothing outside an
  initialized OpenSpec project; it exposes only skill names matching
  `^openspec-[a-z0-9][a-z0-9-]*$` and warns when the `openspec` CLI is absent.
- **Graceful degradation.** Every interactive feature has a fallback:
  no `/dev/tty` → stdin tty → non-interactive; no `termios` (Windows) →
  line-mode prompts; `NO_COLOR`/`TERM=dumb` → no ANSI.
- **Trust model.** Codex approves hook *command definitions*, not file
  contents. Definition changes (e.g., moved paths) require re-trust in
  `/hooks`; content edits do not.

## Platform support

| Platform | Status | Notes |
|---|---|---|
| Linux | Full | Developed and tested here |
| macOS | Full | `/dev/tty`, `termios` cbreak, `~/.local/share` fallback — all POSIX |
| Windows | Degraded but functional | `install.py` runs under Python; `install.sh` needs Git Bash/WSL. No `termios` → line-mode prompts. `commandWindows` is emitted but unverified against Codex-on-Windows |

## Release process

Single source of truth: `VERSION` in `install.py`. To release:

```bash
# bump VERSION first, then:
git -c user.name="Lenin AGC" -c user.email="934933+IGUNUBLUE@users.noreply.github.com" commit -am "..."
git push origin main
git -c user.name="Lenin AGC" -c user.email="934933+IGUNUBLUE@users.noreply.github.com" tag -a vX.Y.Z -m "..."
git push origin vX.Y.Z
gh release create vX.Y.Z --title "vX.Y.Z" --notes "..."
```

Git identity is repo-local per command — do not change global git config.

## Versioning policy

Strict semver on the 1.x line:

- **patch** — fixes and internal/docs changes.
- **minor** — backward-compatible features (new flags, new prompts, new hooks).
- **major** — only for breaking changes to the CLI surface or hook behavior.

`v1.0.0` was tagged early; the project stays on 1.x rather than renumbering,
because every change since has been additive. Do not cut a new major version
without an actual incompatibility.

## Memory system

Persistent project memory lives in `docs/memory/`:

- `current-state.md` — living snapshot of where things stand (release,
  architecture, runtime layout). Rewrite it when state changes; keep the
  "Last review" date current.
- `decisions.md` — append-only decision log. New entries go at the end with
  date, context, decision, and consequences. Never edit or delete prior
  entries — supersede them with a new entry that references the old one.

Update memory in the same commit as the change it describes.
