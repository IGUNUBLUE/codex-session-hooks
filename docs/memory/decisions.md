# Decision log

Append-only record of significant decisions. New entries go at the end.
Never edit or delete an entry — supersede it with a new one that references
the old.

Format: date, context, decision, consequences.

---

## 0001 — Two conditional SessionStart hooks, not one combined script

**Date:** 2026-09-15 (v1.0.0 era)
**Context:** Superpowers must load in every session; OpenSpec only where a
project is initialized. Both are `SessionStart` command hooks.
**Decision:** Ship two independent handlers sharing the
`^(startup|clear|compact)$` matcher. `resume` is excluded — the resumed
transcript already contains the context, re-injecting would duplicate tokens.
**Consequences:** Each hook is approved separately in `/hooks`; each can be
installed or removed independently via `--hooks`.

## 0002 — Ownership identified by marker token, never basename

**Date:** 2026-09-15 (v1.0.0 era)
**Context:** The installer must merge into `hooks.json` without touching
unrelated hooks — including third-party hooks that happen to use similar
script names.
**Decision:** Managed entries carry `--managed-by=codex-session-hooks` in the
command; `managed_hook_id()` matches only on that token. Legacy entries are
recognized by status message + script path under known repo names.
**Consequences:** Re-running the installer from any location rewrites managed
entries cleanly; foreign hooks survive byte-for-byte.

## 0003 — Framework-precedence rule lives in the conditional hook

**Date:** 2026-09-18 (v1.1.0)
**Context:** Superpowers' "1% rule" and OpenSpec's skills overlap in the
same stage (pre-implementation). The conflict only exists inside OpenSpec
projects.
**Decision:** The precedence text is injected by `openspec-context.py` — the
hook that only fires where the collision can occur. It costs zero tokens in
repos without OpenSpec and requires no modification of upstream files.
**Consequences:** Composition rules travel with the context they govern;
repos without OpenSpec see none of it.

## 0004 — Never auto-initialize OpenSpec from a session hook

**Date:** 2026-09-18 (v1.3.0)
**Context:** The combined stack only delivers value where `openspec/` exists,
but a SessionStart hook runs in *every* directory the user opens — clones of
third parties, scratch dirs, repos using other spec frameworks. `openspec
init` writes files into the working tree.
**Decision:** Hooks never write. Per-project setup is an explicit installer
flag: `./install.sh --openspec-init [DIR]` (init if missing, `--force`
update if present, verified afterward).
**Consequences:** Consent is per-project and visible; hooks stay read-only.

## 0005 — npm first for fresh OpenSpec installs; Volta only as owner

**Date:** 2026-09-18 (v1.2.1)
**Context:** The initial fallback order preferred Volta — a personal
toolchain choice of the maintainer, not a general requirement.
**Decision:** For fresh installs prefer `npm` (OpenSpec's documented method,
ships with the required Node), then pnpm, bun, Yarn 1, volta last. When an
existing install is detected under `VOLTA_HOME`, keep updating it with Volta.
**Consequences:** No imposed toolchain; existing ownership is respected.

## 0006 — Standalone installs live in a stable managed directory

**Date:** 2026-09-18 (v1.0.0)
**Context:** `hooks.json` needs absolute paths (SessionStart runs in
arbitrary cwd), so an ephemeral `/tmp` download would break hooks.
**Decision:** `curl | bash` extracts the release tarball into
`${XDG_DATA_HOME:-~/.local/share}/codex-session-hooks` (override:
`CODEX_HOOKS_HOME`) and points hooks there.
**Consequences:** Runtime survives dev-clone moves; self-update re-downloads
the release tarball into the same location.

## 0007 — Guided TUI on stdlib ANSI with total degradation

**Date:** 2026-09-18 (v1.5.0)
**Context:** Guided install needed prompts that work under `curl | bash`
(stdin carries the script, not the keyboard) with zero dependencies.
**Decision:** Hand-rolled clack-style widgets. Prompts read `/dev/tty` first,
stdin-tty fallback; `termios` cbreak (not raw — preserves OPOST so `\n`
doesn't staircase); `os.read` + `select` timeout for arrow escape sequences;
cursor hidden during menus; every widget falls back to line input when key
reporting is unavailable; `NO_COLOR`/`TERM=dumb` honored; Ctrl+C exits 130.
**Consequences:** Same install flow everywhere; worst case is plainer UX,
never broken functionality. Windows runs line-mode prompts.

## 0008 — Stay on the 1.x line with strict semver

**Date:** 2026-09-18
**Context:** `v1.0.0` was tagged before the project had proven stability —
arguably premature for semver's "public API" promise. Six releases exist.
**Decision:** Do not renumber. Stay on 1.x: patch = fixes/docs, minor =
additive features, major = only real incompatibilities. The CLI surface has
in fact stayed backward-compatible since 1.0.0, so no semver violation has
occurred in practice.
**Consequences:** Versioning contract documented in AGENTS.md; major bumps
require an actual breaking change.

## 0009 — Managed runtime dir for users, clone for development

**Date:** 2026-09-18
**Context:** The dev clone moved (`~/codex-session-hooks` →
`~/Projects/codex-session-hooks`), breaking hook paths until the installer
re-ran — because command definitions embed absolute paths.
**Decision:** Recommended runtime mode is the managed dir (decision 0006);
the clone is development-only. Caveat documented: running `install.py` from
the clone repoints hooks at the clone — that is "dev mode", and moving the
clone again requires re-running it.
**Consequences:** Users get set-and-forget hooks; developers opt into
path-fragility knowingly.

## 0010 — Repo-scoped installs; Superpowers via upstream tarball into `.agents/skills`

**Date:** 2026-09-23 (v2.0.0) — supersedes 0006 and 0009
**Context:** The user wants nothing installed globally: hooks, scripts, and
skills must live inside the target repository, and the installer must show
and confirm the target. Official mechanisms verified against Codex docs:
project hooks (`<repo>/.codex/hooks.json`, gated by project-layer trust) and
REPO-scope skills (`<repo>/.agents/skills`, ancestors walked to repo root,
symlinks followed). There is no official repo-scoped *plugin* install —
`codex plugin add` is user-global only — so materialization is the one
custom piece; discovery stays native.
**Decision:**
- Target resolution: `--repo DIR` → git root of cwd → TUI path prompt →
  error. Confirmed before any write.
- Hook commands use the documented `"$(git rev-parse --show-toplevel)/…"`
  form (double-quoted so substitution survives); absolute fallback for
  non-git `--repo` targets.
- Hook scripts are *copied* into `<repo>/.codex/hooks/` — the repo is
  self-contained.
- Superpowers skills materialize from the `obra/superpowers` release tarball
  (stdlib `urllib`+`tarfile`, sanitized members) into `.agents/skills/`,
  tracked by `.agents/skills/.codex-session-hooks.json` for clean
  update/remove. Chosen over git submodule (less ceremony) and symlinks to a
  shared checkout (would reintroduce a global dependency).
- `install.sh` piped mode downloads to `mktemp` — transient, no managed dir.
- Managed entries previously written to `~/.codex/hooks.json` are detected
  and offered for removal; `[features] hooks=true` in `config.toml` stays —
  it is a user capability flag, not a repo artifact.
- Generated files default to local via a managed `.gitignore` block; TUI
  offers committing them for team sharing.
**Consequences:** Breaking change → v2.0.0. Each repo carries its own hooks
and skills; teams can commit them. First `SessionStart` in a repo is skipped
until `/hooks` approval (openai/codex#35306) and worktrees don't load
project hooks (openai/codex#27133) — documented, not worked around.

## 0011 — Multi-harness: shared Python handlers + thin native TS adapters

**Date:** 2026-09-23 (v2.1.0)
**Context:** User asked for OpenCode v2 and oh-my-pi coverage using official
mechanisms. Research found the equivalents: OpenCode auto-discovers
`.opencode/plugins/*.ts` and exposes `ctx.session.hook("context")` with
`event.system.push`; oh-my-pi auto-discovers `.omp/extensions/*.ts` and
exposes a `context` event that may return a modified message list. Both
discover `.agents/skills` natively, so materialized Superpowers skills need
no extra work. OpenSpec supports `--tools opencode` and `--tools oh-my-pi`.
**Decision:**
- `--harness codex,opencode,omp` (`all`/`none`); default = detected CLIs.
  `--hooks` picks contexts, `--harness` picks destinations — full matrix.
- Handlers move to `.agents/session-hooks/` — one source of logic; every
  adapter just spawns them and caches stdout per session.
- OpenCode adapter is a plain `export default {id, setup}` object. The
  documented `import { Plugin } from "@opencode/plugin"` fails to resolve
  in auto-discovered project plugins (verified on v2.0.15) — plain objects
  ride the v1/v2 bridge.
- omp adapter injects a deduplicated `custom` message inside `pi.on(
  "context")` — idempotent across resume/branch/compact; lazy-cached so it
  works regardless of `session_start` ordering.
- Adapter files are never gitignored: omp's extension-module discovery uses
  a gitignore-aware glob (verified live — an ignored adapter is invisible);
  its skill discovery ignores `.gitignore`, so materialized skills may stay
  ignored.
- OpenSpec init maps harnesses to tool names (`omp` → `oh-my-pi`) and runs
  once with the joined list.
**Consequences:** v2.1.0 minor — `--harness codex` behaves like v2.0 modulo
the script path move (definition change → Codex re-trust needed, as with any
moved path). Zero-adapter global state; adapters are regenerated/deleted as
units. `openspec-context.py` now reports `opsx-*` commands too.
