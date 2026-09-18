# codex-session-hooks

`SessionStart` hooks for [Codex CLI](https://github.com/openai/codex) that force-inject framework bootstraps into every session — so agentic frameworks actually activate instead of waiting for the model to notice them.

Currently ships two hooks:

| Hook | Injects | When |
|------|---------|------|
| `session-start.sh` | [Superpowers](https://github.com/obra/superpowers) `using-superpowers` bootstrap | Always (if Superpowers is installed) |
| `openspec-detect.sh` | OpenSpec bootstrap + list of in-flight changes | Only in repos with an `openspec/` directory |

## Why

Agentic frameworks distributed as skills rely on *soft enforcement*: the harness injects each skill's name and description into context, and the model decides whether to read the full instructions. If it doesn't, the framework never activates.

- **Superpowers** ships `"hooks": {}` in its Codex plugin manifest — nothing forces the `using-superpowers` bootstrap (the skill that mandates checking skills before *any* response) into context. In Claude Code a `SessionStart` hook does this; this repo is the missing Codex equivalent.
- **OpenSpec** writes `.agents/skills/openspec-*/SKILL.md` and a managed `AGENTS.md` stub, so the agent usually knows it exists — but the stub is shallow, and ambient behaviors (checking `openspec/changes/` before touching covered behavior, keeping specs in sync) still depend on the model choosing to read more.

Codex adds anything a `SessionStart` hook prints to stdout as developer context. These hooks use that to guarantee the bootstraps are present on `startup`, `resume`, `clear`, and `compact` — no model cooperation required.

## Prerequisites

Install each framework per its own documentation — the hooks only inject context, they don't install anything.

### Codex CLI

Hooks support requires a recent Codex CLI and is on by default. Verify in `~/.codex/config.toml`:

```toml
[features]
hooks = true
```

Optional, needed by Superpowers skills that dispatch subagents (`subagent-driven-development`, `dispatching-parallel-agents`):

```toml
[features]
multi_agent = true
```

### Superpowers

From the [official Codex plugin marketplace](https://github.com/openai/plugins), inside Codex:

```text
/plugins
```

Search for `superpowers` and select **Install Plugin**. This lands the plugin under `~/.codex/plugins/cache/` — `session-start.sh` finds it there automatically (newest version wins; override with `$SUPERPOWERS_USING_SKILL` pointing at a `using-superpowers/SKILL.md`).

Upstream docs: <https://github.com/obra/superpowers>

### OpenSpec

Per the [OpenSpec docs](https://github.com/Fission-AI/OpenSpec):

```bash
# 1. Install the CLI (terminal)
npm install -g @fission-ai/openspec@latest

# 2. Initialize inside each project (terminal)
cd your-project && openspec init    # select Codex when prompted for tools
```

`openspec init` creates `openspec/` and `.agents/skills/openspec-*/` in the project. The hook only activates in repos where `openspec/` exists — in every other project it exits silently and injects nothing.

### System tools

`bash`, `find`, `sort` (GNU coreutils), `git`. `python3` is required by `install.sh` only.

## Install

```bash
git clone https://github.com/IGUNUBLUE/codex-session-hooks
cd codex-session-hooks
./install.sh
```

`install.sh` merges a `SessionStart` matcher group per script into `~/.codex/hooks.json` without touching existing hooks, backs up the file first (`hooks.json.bak.*`), and is idempotent — re-running it after moving this repo fixes stale command paths in place.

Then:

1. Start a new Codex session.
2. Run `/hooks` and mark the new hooks as trusted — Codex skips untrusted hooks (trust is recorded against the hook's hash, so edits require re-approval).

### Two separate handlers, on purpose

Each script gets its own hook entry rather than one combined script because Codex runs same-event hooks concurrently and lets you trust/disable handlers individually — so you get failure isolation, separate `additionalContextLimit` budgets, and granular control in `/hooks` at essentially zero cost.

### Prefer inline TOML?

The equivalent for `~/.codex/config.toml` (skip `install.sh` and `hooks.json` entirely):

```toml
[[hooks.SessionStart]]
matcher = "startup|resume|clear|compact"

[[hooks.SessionStart.hooks]]
type = "command"
command = "bash /path/to/codex-session-hooks/session-start.sh"
statusMessage = "Loading superpowers"
additionalContextLimit = 8000

[[hooks.SessionStart]]
matcher = "startup|resume|clear|compact"

[[hooks.SessionStart.hooks]]
type = "command"
command = "bash /path/to/codex-session-hooks/openspec-detect.sh"
statusMessage = "Loading openspec context"
additionalContextLimit = 8000
```

## Verify

Start a new Codex session inside a project that uses OpenSpec and ask the model what it knows about OpenSpec and Superpowers — it should describe both without being asked to read anything.

## Uninstall

Remove the `SessionStart` matcher groups that reference `session-start.sh` / `openspec-detect.sh` from `~/.codex/hooks.json` (or restore a `hooks.json.bak.*` backup), then reopen `/hooks` so Codex re-scans.

## License

MIT
