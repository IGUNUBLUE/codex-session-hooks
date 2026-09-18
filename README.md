# codex-session-hooks

Small, auditable `SessionStart` hooks for [Codex CLI](https://github.com/openai/codex). They automatically add framework bootstrap text as developer context, improving activation reliability without claiming that an LLM can be forced to obey it.

| Hook | Behavior |
|---|---|
| `superpowers-bootstrap.py` | Loads `using-superpowers/SKILL.md` only from the installed, enabled Superpowers plugin version reported by Codex. |
| `openspec-context.py` | Finds the nearest ancestor containing `openspec/config.yaml` and advertises only the OpenSpec skills that actually exist. It does not expose repository-controlled change names or impose OpenSpec on unrelated work. |

The legacy shell entry points remain as thin compatibility wrappers, but new installations invoke the Python hooks directly.

## Why

Codex skills use progressive disclosure: their names and descriptions are initially visible, while Codex loads the full `SKILL.md` only when the skill is selected explicitly or matched implicitly. That is intentional, but an always-on bootstrap such as Superpowers' `using-superpowers` skill benefits from automatic context injection.

Superpowers ships a `SessionStart` hook for other harnesses, while its current Codex plugin manifest declares `"hooks": {}`. This repository adds the missing Codex-side bootstrap without forking Superpowers.

OpenSpec is different: its workflows are designed for explicit skill invocation. The OpenSpec hook therefore stays informational and conditional. It detects the nearest initialized OpenSpec project, discovers the exact generated skill names (including current `.agents/skills` and legacy `.codex/skills` locations), and tells Codex to follow the selected skill rather than assuming a fixed artifact layout.

Codex documents plain `SessionStart` stdout as extra developer context. These hooks run on `startup`, `clear`, and after `compact`. They intentionally skip `resume` to avoid duplicating context already stored in a resumed transcript.

## Prerequisites

- A recent Codex CLI exposing `codex plugin` and lifecycle hooks.
- Python 3.10 or newer.
- For OpenSpec: Node.js 20.19.0 or newer and one supported global package manager (`volta`, `npm`, `pnpm`, `bun`, or Yarn 1).
- `bash` is optional and used only by the Unix convenience wrappers. Native Windows users can run `python install.py`.

## Install

One line, no clone required:

```bash
curl -fsSL https://raw.githubusercontent.com/IGUNUBLUE/codex-session-hooks/main/install.sh | bash
```

The standalone installer downloads the repository into `${XDG_DATA_HOME:-~/.local/share}/codex-session-hooks` and configures the hooks to run from that stable path. Re-running the same command upgrades to the latest `main`. Because the hook command definitions keep pointing at the same paths, no `/hooks` re-approval is needed after an upgrade — but review repository changes before pulling them.

Useful variations:

```bash
# Forward installer flags through bash -s --
curl -fsSL .../install.sh | bash -s -- --hooks openspec

# Pin a release tag instead of tracking main
curl -fsSL .../install.sh | env CODEX_HOOKS_REF=v1.0.0 bash

# Change where the repository is installed
curl -fsSL .../install.sh | env CODEX_HOOKS_HOME=/opt/codex-session-hooks bash
```

As with any piped installer, review `install.sh` before running it.

From a checkout (works the same as before):

```bash
git clone https://github.com/IGUNUBLUE/codex-session-hooks
cd codex-session-hooks
./install.sh
```

On Windows:

```powershell
python install.py
```

By default, the installer:

1. Discovers Superpowers in the official Codex marketplace, runs the idempotent `codex plugin add <discovered-plugin-id>` command, and verifies that it is installed and enabled. The version is the latest release resolved by that marketplace at install time.
2. Detects the package manager that owns OpenSpec (including Volta), installs `@fission-ai/openspec@latest`, queries the registry, and verifies that `openspec --version` matches it.
3. Enables Codex hooks through `codex features enable hooks`.
4. Atomically merges both handler definitions into `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`). Existing unrelated hooks are preserved exactly. Changed files receive a unique timestamped backup.
5. Migrates only hook entries previously owned by this project; similarly named third-party hooks are never matched by basename alone.

After installation, start a new Codex session, run `/hooks`, review the changed definitions, and trust them. Codex intentionally skips untrusted user hooks. Trust covers the command definition, not future changes to the target script, so review repository updates before pulling them.

### Select handlers

```bash
# Install/update only Superpowers
./install.sh --hooks superpowers

# Install/update only OpenSpec
./install.sh --hooks openspec

# Configure hooks without updating either framework
./install.sh --skip-framework-updates

# Remove only this project's OpenSpec hook
./install.sh --hooks none --remove openspec --skip-framework-updates

# Remove all hooks owned by this project
./install.sh --hooks none --remove all --skip-framework-updates
```

If OpenSpec is owned by a particular package manager, override detection:

```bash
./install.sh --openspec-package-manager pnpm
```

## Framework setup

### Superpowers

The installer uses the official Codex marketplace automatically. For manual installation, open `/plugins` inside Codex, search for `superpowers`, and select **Install Plugin**. Automation can discover the current official marketplace selector with `codex plugin list --available --json` and pass its `pluginId` to `codex plugin add`.

Inspect the authoritative installed/enabled state with:

```bash
codex plugin list --json
```

Upstream: <https://github.com/obra/superpowers>

### OpenSpec

The installer updates the global CLI. To initialize a repository for current Codex skill delivery:

```bash
cd your-project
openspec init --tools codex
```

Current OpenSpec writes repo-local skills under `.agents/skills/openspec-*/`, invoked in Codex with `$openspec-<skill>`. Names do not always match the canonical OPSX command: examples include `$openspec-propose`, `$openspec-apply-change`, `$openspec-sync-specs`, and `$openspec-archive-change`. The hook discovers the installed names instead of hard-coding them.

After upgrading the OpenSpec CLI, refresh each initialized project deliberately:

```bash
cd your-project
openspec update
```

This is not run automatically by the hook installer because OpenSpec updates may migrate generated files and remove obsolete managed integrations. Review each project and its version-control diff separately.

Upstream: <https://github.com/Fission-AI/OpenSpec>

## Configuration behavior

The generated definitions use:

```json
{
  "matcher": "^(startup|clear|compact)$",
  "hooks": [{
    "type": "command",
    "command": "<quoted Python executable> <quoted hook path> --managed-by=codex-session-hooks",
    "commandWindows": "<Windows equivalent>",
    "timeout": 15,
    "additionalContextLimit": 2500
  }]
}
```

The ownership marker allows safe idempotent upgrades and removal. Paths are shell-quoted, so cloning into a directory containing spaces is supported.

## Security model

- Hook output is instructions, not deterministic enforcement; model behavior can still vary.
- The OpenSpec hook never inserts change-directory names or artifact contents into developer context. Skill names must match `openspec-[a-z0-9-]+`.
- The Superpowers hook consults `codex plugin list --json` and loads the exact active marketplace version. Disabled or stale cached copies are ignored.
- Both hooks cap their context and the Superpowers loader rejects unexpectedly large bootstrap files.
- Hooks execute local scripts with your account's permissions. Review this repository before installation and before pulling updates.

## Verify

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile install.py superpowers-bootstrap.py openspec-context.py
bash -n install.sh session-start.sh openspec-detect.sh
python3 -m json.tool hooks.json >/dev/null
```

Runtime checks:

```bash
python3 superpowers-bootstrap.py
cd /path/to/nested/open-spec/project/subdirectory
python3 /path/to/codex-session-hooks/openspec-context.py
```

The OpenSpec command should print nothing outside an initialized project.

## License

MIT
