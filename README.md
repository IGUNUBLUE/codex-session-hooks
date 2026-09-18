# codex-superpowers-hook

A `SessionStart` hook for [Codex CLI](https://github.com/openai/codex) that force-injects the [Superpowers](https://github.com/obra/superpowers) `using-superpowers` bootstrap into every session.

## Why

The Superpowers plugin for Codex ships `"hooks": {}` — no lifecycle hooks. That means the `using-superpowers` bootstrap (the skill that tells the agent it *must* check and invoke skills before responding) is only triggered if the model decides to read it on its own. Soft enforcement.

In Claude Code, a `SessionStart` hook injects that bootstrap automatically — including after compaction. Codex supports the exact same mechanism; this repo is just the missing piece so you don't have to fork all of Superpowers to get it.

## What it does

On `startup`, `resume`, `clear`, and `compact`, Codex runs `session-start.sh`, which:

1. Locates the newest installed Superpowers plugin under `~/.codex/plugins/cache/*/superpowers/*/skills/using-superpowers/SKILL.md` (override with `$SUPERPOWERS_USING_SKILL`, or falls back to `~/.codex/skills/using-superpowers/SKILL.md`).
2. Prints it to stdout — Codex adds that output as extra developer context.
3. Exits 0 silently if Superpowers isn't installed, so sessions still start.

## Install

```bash
git clone https://github.com/IGUNUBLUE/codex-superpowers-hook
cd codex-superpowers-hook
./install.sh
```

`install.sh` merges a `SessionStart` entry into `~/.codex/hooks.json` without touching existing hooks, backs up the file first, and is idempotent. It produces this entry:

```json
{
  "matcher": "startup|resume|clear|compact",
  "hooks": [{
    "type": "command",
    "command": "bash /path/to/codex-superpowers-hook/session-start.sh",
    "statusMessage": "Loading superpowers",
    "additionalContextLimit": 8000
  }]
}
```

Then:

1. Start a new Codex session.
2. Run `/hooks` and mark **"Loading superpowers"** as trusted — Codex skips untrusted hooks (trust is recorded against the hook's hash, so edits require re-approval).
3. Ensure hooks are enabled in `~/.codex/config.toml` (default is on):

   ```toml
   [features]
   hooks = true
   ```

Prefer inline TOML over `hooks.json`? The equivalent for `~/.codex/config.toml`:

```toml
[[hooks.SessionStart]]
matcher = "startup|resume|clear|compact"

[[hooks.SessionStart.hooks]]
type = "command"
command = "bash /path/to/codex-superpowers-hook/session-start.sh"
statusMessage = "Loading superpowers"
additionalContextLimit = 8000
```

## Optional: subagent support

Skills like `subagent-driven-development` need Codex's multi-agent tools:

```toml
[features]
multi_agent = true
```

## Uninstall

Remove the `SessionStart` matcher group that references `session-start.sh` from `~/.codex/hooks.json` (restore a `hooks.json.bak.*` backup if needed), then reopen `/hooks` so Codex re-scans.

## Requirements

- Codex CLI with hooks support (`features.hooks`, enabled by default)
- [Superpowers](https://github.com/obra/superpowers) installed via `/plugins`
- `bash`, `find`, `sort` (GNU coreutils), `python3` (install script only)

## License

MIT
