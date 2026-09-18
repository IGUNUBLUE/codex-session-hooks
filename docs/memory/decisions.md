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
