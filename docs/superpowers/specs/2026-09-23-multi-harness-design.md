# Multi-harness install — design spec

> Status: draft, awaiting review. Target release: v2.1.0 (additive, backward
> compatible with v2.0.0 behavior when `--harness codex`).

## Context and motivation

v2.0.0 made the install repo-scoped for Codex. The user now runs more than one
coding harness — OpenCode 2 and oh-my-pi (omp) — and wants the same
superpowers/OpenSpec session context available in each, still using official
documented mechanisms first and custom code only for the gaps.

## Official mechanisms this design relies on

Verified against https://opencode.ai/v2/docs (OpenCode v2.0.15 installed),
https://github.com/can1357/oh-my-pi docs (omp 18.1.21 installed), and
`openspec init --help` (1.13.1):

| Concern | Codex (existing) | OpenCode v2 | oh-my-pi |
|---|---|---|---|
| Repo-scoped hook carrier | `.codex/hooks.json` (project layer trust + `/hooks` approval) | `.opencode/plugins/*.ts` — auto-discovered, no config file, no trust gate | `.omp/extensions/*.ts` — auto-discovered, no config file |
| Session-context injection | `SessionStart` command hook, matcher `^(startup\|clear\|compact)$` | `ctx.session.hook("context")` → `event.system.push({type:"text", text})`; runs per model call, survives compaction | `pi.on("context")` → `{messages}` rewrite with dedup; survives compaction and resume |
| Repo skills | `.agents/skills` | `.agents/skills` (official compat source) + `.opencode/skills` | `.agents/skills` (agents-compat provider) + `.omp/skills` (native, recursive) |
| OpenSpec | `openspec init --tools codex` → `.agents/skills/openspec-*` | `--tools opencode` → `.opencode/commands/opsx-*.md` | `--tools oh-my-pi` → `.omp/commands/opsx-*.md` |
| Capability flag | `codex features enable hooks` (only global state) | none | none |

Key consequence: **the superpowers skills already materialized into
`.agents/skills/` are discovered natively by all three harnesses** — no
per-harness skill work. The only custom code is the two thin TypeScript
adapters and the installer logic.

## Goals

- `install.py` supports `--harness codex,opencode,omp` (comma-separated,
  `all`/`none`). Default: harness CLIs detected in `PATH` (TUI multiselect;
  `-y` accepts all detected).
- One shared implementation of hook logic (the Python handlers) consumed by
  every harness; adapters are thin shells that spawn `python3 <script>` once
  per session and cache the output.
- Nothing global beyond the existing `codex features enable hooks` capability
  flag — and only when `codex` is selected.
- `openspec init --tools` runs once with the per-harness tool list
  (`codex`→`codex`, `opencode`→`opencode`, `omp`→`oh-my-pi`).
- Ownership/marker semantics preserved: unrelated files in `.opencode/`,
  `.omp/`, `.agents/` are never touched.

## Non-goals

- No pure-TS reimplementation of handler logic (rejected: duplicates
  ancestor-walk, openspec detection, and the 8000-char cap — drift risk).
- No support for OpenCode v1 plugin API, upstream `pi`, or other harnesses.
- No per-harness superpowers skill variants — `.agents/skills` covers all.
- No `sendMessage`-based omp injection (would duplicate on resumed sessions;
  the `context` rewrite is idempotent).

## Design

### Repository layout after install

```
<repo>/
├── .agents/
│   ├── session-hooks/              # NEW — shared Python handlers
│   │   ├── superpowers-bootstrap.py
│   │   └── openspec-context.py
│   ├── skills/<superpowers-*>      # unchanged; serves all three harnesses
│   └── skills/.codex-session-hooks.json   # unchanged manifest
├── .codex/hooks.json               # codex: commands → .agents/session-hooks/
├── .opencode/plugins/session-hooks.ts    # opencode adapter
└── .omp/extensions/session-hooks.ts      # omp adapter
```

### Shared handler scripts

The Python handlers move from `.codex/hooks/` to `.agents/session-hooks/` —
a neutral location since they now serve every harness. On install, any
v2.0.0-era managed scripts left under `.codex/hooks/` are removed (the dir
is deleted if left empty); `.codex/hooks.json` command paths are regenerated
so the file still matches the trust-approved definitions it describes.

Both handlers keep their current behavior and `--managed-by` marker arg;
their ancestor-walk already starts from `Path(__file__)` so they work from
the new location unchanged.

### OpenCode adapter (`.opencode/plugins/session-hooks.ts`)

```ts
// managed-by: codex-session-hooks
import { Plugin } from "@opencode/plugin"
export default Plugin.define({
  id: "codex-session-hooks",
  async setup(ctx) {
    // spawn each enabled python handler once; cache stdout per script
    await ctx.session.hook("context", (event) => {
      for (const text of cachedOutputs)
        event.system.push({ type: "text", text })
    })
  },
})
```

- Scripts run via `node:child_process` (`python3 <abs path>
  --managed-by=codex-session-hooks`), cwd = `ctx.location.directory`.
- Output cached at setup; empty stdout → contributes nothing (silent).
- `context` runs on every agent-loop model call, so injected context
  persists across compaction — matching our `compact` matcher semantics.
- Implementation risk noted below: verify `@opencode/plugin` resolves in
  auto-discovered plugins and confirm system-push behavior on v2.0.15.

### omp adapter (`.omp/extensions/session-hooks.ts`)

```ts
// managed-by: codex-session-hooks
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
export default function (pi: ExtensionAPI) {
  pi.on("context", async (event) => {
    // lazily spawn python handlers on first call; cache outputs
    // if a message with our customType already exists → return undefined
    // else return { messages: [contextMsg, ...event.messages] }
  });
}
```

- Lazy init inside `context` avoids depending on `session_start` ordering.
- Dedup by `customType` marker makes injection idempotent across resume,
  branching, and compaction — the message is re-added only if gone.
- `pi.exec`/child_process for the spawn; `ctx.cwd` for script-dir resolution
  (walk ancestors for `.agents/session-hooks/`, same rule as the Python
  scripts themselves).

### Harness selection and CLI

- `--harness` accepts comma-separated `codex,opencode,omp` plus `all`/`none`.
- Without the flag: detected CLIs (`shutil.which("codex"/"opencode"/"omp")`)
  drive the TUI multiselect; `-y` accepts all detected; none detected →
  default `codex` (preserves v2.0.0 behavior on codex-only machines).
- Explicit `--harness opencode` without the binary warns but still installs
  (files are valid for whenever the CLI lands).
- `--remove` still selects hook ids and applies inside every installed
  harness: each run regenerates the adapters to contain only the selected
  hooks, so a removed hook disappears from `.codex/hooks.json` and from the
  spawned-script lists alike.
- A harness deselected via `--harness` (installed before, not selected now)
  gets its adapter file deleted. An adapter left with zero hooks is deleted
  too — an empty adapter is a no-op we do not keep.

### OpenSpec

`openspec init --tools <joined>` where the selected harnesses map to
`codex`,`opencode`,`oh-my-pi`. Unchanged consent rules: only via
`--openspec-init` or the explicit TUI confirm; never from a hook.

`openspec-context.py` gains optional awareness of opsx **commands**
(`.opencode/commands/opsx-*.md`, `.omp/commands/opsx-*.md`) so its "generated
artifacts" line reflects the harness layout — the precedence rule and
project-root detection stay identical.

### `.gitignore` managed block

Adds per selected harness: `/.agents/session-hooks/`,
`/.opencode/plugins/session-hooks.ts`, `/.omp/extensions/session-hooks.ts`.
The existing `.codex/*` entries stay conditional on `codex`.

### Ownership

- TS adapters: whole-file ownership via the `managed-by` header comment;
  installer rewrites/removes them wholesale. Foreign files in
  `.opencode/plugins/` / `.omp/extensions/` untouched.
- Python handlers + `hooks.json` entries: unchanged marker/merge semantics.

## Migration and compatibility

- Re-running v2.1.0 on a v2.0.0 install moves handlers to
  `.agents/session-hooks/`, regenerates `.codex/hooks.json` commands, and
  leaves an empty `.codex/hooks/` removed. Hook *definitions* change → Codex
  `/hooks` re-trust required (documented invariant, not a bug).
- `--harness codex` alone reproduces v2.0.0 output except for the script
  path change — treated as part of the same feature.

## Testing

- Unit: harness parsing, detection defaults, per-harness file generation,
  `.codex/hooks/` migration, gitignore matrix, removal paths.
- Real-machine: install with all three harnesses into a scratch repo; run
  `opencode` and `omp` headless (`opencode run`, `omp -p`) and confirm the
  context reaches the model; verify silent behavior outside OpenSpec repos.

## Versioning

v2.1.0 — additive feature. `--harness` defaults preserve current outcomes;
the only behavior change under `--harness codex` is the handler script
location (internal detail, regenerated atomically).
