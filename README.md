# codex-session-hooks

Small, auditable session-context hooks for [Codex CLI](https://github.com/openai/codex), [OpenCode](https://opencode.ai), and [oh-my-pi](https://github.com/can1357/oh-my-pi). They automatically add framework bootstrap text as developer context, improving activation reliability without claiming that an LLM can be forced to obey it.

| Hook | Behavior |
|---|---|
| `superpowers-bootstrap.py` | Loads `using-superpowers/SKILL.md` from the repo's `.agents/skills/` (materialized from the upstream `obra/superpowers` release), falling back to user-level skill dirs. |
| `openspec-context.py` | Finds the nearest ancestor containing `openspec/config.yaml` and advertises only the OpenSpec skills that actually exist. Warns when the `openspec` CLI is not on PATH, and declares the framework precedence rule below. It does not expose repository-controlled change names or impose OpenSpec on unrelated work. |

The legacy shell entry points remain as thin compatibility wrappers, but new installations invoke the Python hooks directly.

## Why

Agent skills use progressive disclosure: their names and descriptions are initially visible, while the full `SKILL.md` loads only when the skill is selected explicitly or matched implicitly. That is intentional, but an always-on bootstrap such as Superpowers' `using-superpowers` skill benefits from automatic context injection.

Superpowers ships a `SessionStart` hook for other harnesses, while its current Codex plugin manifest declares `"hooks": {}`. This repository adds the missing bootstrap without forking Superpowers — the installer downloads the upstream release tarball and materializes its `skills/` tree into the repo's `.agents/skills/`, where Codex, OpenCode, and oh-my-pi all discover skills natively.

OpenSpec is different: its workflows are designed for explicit skill invocation. The OpenSpec hook therefore stays informational and conditional. It detects the nearest initialized OpenSpec project, discovers the exact generated skill or `opsx-*` command names, and tells the agent to follow them rather than assuming a fixed artifact layout.

How the context reaches the model depends on the harness — each uses its official repo-scoped mechanism: Codex runs the Python handlers as `SessionStart` command hooks (`startup`, `clear`, `compact`; `resume` intentionally skipped); OpenCode loads `.opencode/plugins/session-hooks.ts`, which caches the handlers' stdout and pushes it into `event.system` on every model call; oh-my-pi loads `.omp/extensions/session-hooks.ts`, which injects it as a deduplicated custom message via the `context` event. Both adapters survive compaction and resume without duplicating.

## Superpowers + OpenSpec composition

The two frameworks operate on different axes. Superpowers is a development *process* discipline (brainstorm, plan, TDD, debug, verify, review); OpenSpec is an *artifact* governance layer (`specs/` as source of truth, `changes/` as deltas, `archive` as merge). Their skill descriptions overlap at the ideation and planning stages, where both claim first position — and Superpowers' bootstrap pushes automatic invocation while OpenSpec skills are designed for explicit invocation.

The OpenSpec hook resolves this by injecting an explicit precedence rule whenever it detects an OpenSpec project: **for work that creates or modifies specified behavior, the OpenSpec change artifacts are the system of record**. Process skills from other frameworks may still apply *inside* a change for implementation discipline (testing, debugging, review), but documents they generate must reference the OpenSpec spec rather than restate or replace it. The rule rides in the conditional hook, so it costs zero context in projects without OpenSpec.

Two upstream design choices make this composition safe:

- Every generated OpenSpec skill carries an auto-selected escape hatch: if it was triggered without an explicit OpenSpec request, it instructs the agent to answer normally rather than imposing the spec workflow.
- Superpowers' own bootstrap declares that user instructions (`AGENTS.md`, direct requests) take precedence over skills — so repository rules can still override this default ordering.

The repository manages the whole stack, not just the text layer: the installer materializes the Superpowers skills into the repo (pinned upstream release, manifest-tracked) and keeps the OpenSpec CLI current (latest registry version through the owning package manager), and the hook verifies the CLI is on PATH at session time. Nothing in either upstream framework is modified — the composition is added purely through developer context, and the model interprets and combines both skill sets organically.

## Prerequisites

The installer verifies these and fails with a clear error if they are missing — it does not install them:

- At least one supported harness CLI: `codex` (with lifecycle hooks support), `opencode` v2, or `omp`. Harnesses are auto-detected; `--harness` selects explicitly.
- Python 3.10 or newer.
- `git` — Codex hook commands resolve via `git rev-parse --show-toplevel`.
- For OpenSpec: Node.js 20.19.0 or newer and one supported global package manager (`npm`, `pnpm`, `bun`, Yarn 1, or `volta`).
- `bash` is optional and used only by the Unix convenience wrappers. Native Windows users can run `python install.py`.

## Install

Everything installs **into a repository**: the shared Python handlers land in `<repo>/.agents/session-hooks/`, Superpowers skills in `<repo>/.agents/skills/`, and per-harness wiring in `<repo>/.codex/hooks.json`, `<repo>/.opencode/plugins/session-hooks.ts`, and `<repo>/.omp/extensions/session-hooks.ts`. Nothing is written to `~/.codex/hooks.json` or any global plugin/extension directory.

Run it from inside the repo you want to equip, no clone required:

```bash
cd your-project
curl -fsSL https://raw.githubusercontent.com/IGUNUBLUE/codex-session-hooks/main/install.sh | bash
```

The standalone installer downloads this repository to a temporary directory and runs it against your current repo — the download is transient because hook commands resolve their scripts via `git rev-parse --show-toplevel`. The installer always shows the resolved target and asks before writing.

Useful variations:

```bash
# Forward installer flags through bash -s --
curl -fsSL .../install.sh | bash -s -- --hooks openspec

# Pin an installer release tag instead of tracking main
curl -fsSL .../install.sh | env CODEX_HOOKS_REF=v2.0.0 bash

# Explicit target repo (also the only option for non-git directories)
./install.sh --repo /path/to/repo
```

As with any piped installer, review `install.sh` before running it.

From a checkout:

```bash
git clone https://github.com/IGUNUBLUE/codex-session-hooks
cd your-project
/path/to/codex-session-hooks/install.sh        # resolves the git root of the cwd
```

On Windows:

```powershell
python install.py --repo C:\path\to\repo
```

By default, the installer:

1. Resolves the target repo (`--repo`, else the git root of the cwd) and asks for confirmation.
2. Selects harnesses: every detected CLI (`codex`, `opencode`, `omp`) is preselected in the TUI; `-y` accepts all detected, `--harness` overrides. Selecting a harness whose CLI is absent warns but still installs the files.
3. Downloads the pinned `obra/superpowers` release tarball and materializes its `skills/` tree into `<repo>/.agents/skills/` — official skill discovery in all three harnesses. A manifest (`.agents/skills/.codex-session-hooks.json`) records the ref and managed skill dirs so updates and removal stay clean.
4. Detects the package manager that owns an existing OpenSpec installation (including Volta) and updates it in place; for a fresh install it uses the first available of `npm`, `pnpm`, `bun`, Yarn 1, or `volta`. Installs `@fission-ai/openspec@latest`, queries the registry, and verifies that `openspec --version` matches it.
5. Copies the shared hook scripts into `<repo>/.agents/session-hooks/` and writes each selected harness's wiring: Codex gets both handler definitions atomically merged into `<repo>/.codex/hooks.json` (existing unrelated hooks preserved exactly, timestamped backup on change) plus `codex features enable hooks` — the only thing still written outside the repo. OpenCode and oh-my-pi get their managed adapter file (`.opencode/plugins/session-hooks.ts`, `.omp/extensions/session-hooks.ts`); adapters are regenerated to reflect the selected hooks, and deselected harnesses lose their managed adapter. Foreign files in those directories are never touched.
6. Offers to keep generated files local via a managed `.gitignore` block (the default) — or leave them commit-able for the whole team. Adapter files are never ignored: oh-my-pi's extension discovery honors `.gitignore`, so an ignored adapter would be invisible.
7. Detects hooks this project previously installed in `~/.codex/hooks.json` and offers to remove them; similarly named third-party hooks are never matched by basename alone.

### Guided mode

When a terminal is available, the installer runs an interactive TUI: a plan panel explains what it will do before anything runs, the resolved target repo is shown and confirmed, steps animate with a spinner during long operations, and every decision is a prompt — arrow-key multiselects for the hooks and the harnesses (`↑/↓` move, `space` toggles, `a` toggles all, `enter` confirms) and single-keypress `y`/`n` confirms for the rest (whether to update frameworks, whether generated files stay local via `.gitignore`, whether to initialize OpenSpec in the repo, whether to remove previously installed global hooks). Ctrl+C aborts cleanly.

Prompts read `/dev/tty`, so they still work when the script is piped through `curl | bash`; environments without a controlling terminal fall back to plain line input. Colors honor `NO_COLOR` and `TERM=dumb`.

- `-y` / `--yes` accepts every default without asking — the non-interactive behavior, suitable for scripts.
- `--skip-update-check` skips the release check entirely.

### Installer updates

Each run compares the embedded version against the latest GitHub release. When a newer one exists, the installer offers to update itself — `git pull --ff-only` for checkouts, re-downloading the release tarball for standalone installs — and re-executes so the new code continues the run. The first run asks whether to do this automatically on future runs; the answer is stored in `$CODEX_HOME/codex-session-hooks.json`.

After installation, open a session in that repo. For Codex specifically: trust the project `.codex/` layer, run `/hooks`, review the changed definitions, and trust them — Codex intentionally skips untrusted project hooks, so the first `SessionStart` in a freshly trusted repo is skipped once; later sessions get the context normally. Codex project hooks are also ignored inside git worktrees (upstream limitation). Trust covers the command definition, not future changes to the target script, so review repository updates before pulling them. OpenCode and oh-my-pi need no approval step — their adapters load on the next session automatically.

### Select handlers and harnesses

```bash
# Install/update only Superpowers in this repo
./install.sh --hooks superpowers

# Install/update only OpenSpec in this repo
./install.sh --hooks openspec

# Configure hooks without updating either framework
./install.sh --skip-framework-updates

# Install for specific harnesses (default: all detected CLIs)
./install.sh --harness codex,omp
./install.sh --harness all

# Remove only this project's OpenSpec hook from every harness in the repo
./install.sh --hooks none --remove openspec --skip-framework-updates

# Remove all hooks owned by this project from the repo
./install.sh --hooks none --remove all --skip-framework-updates

# Pin a specific upstream Superpowers ref instead of the latest release
./install.sh --superpowers-ref v6.4.1
```

Auto-detection respects the manager that owns an existing installation. To force a specific one — for example `volta` on a machine that also has npm — override it:

```bash
./install.sh --openspec-package-manager pnpm
```

## Framework setup

### Superpowers

The installer downloads the latest `obra/superpowers` release tarball and copies its `skills/` tree into `<repo>/.agents/skills/`, where Codex discovers them natively at REPO scope (symlinks also work, but copies keep the repo self-contained). The manifest records the resolved ref; re-running the installer syncs skills to the newest release and prunes ones that disappeared upstream. `--superpowers-ref <tag-or-sha>` pins a specific version.

To update: re-run the installer. To remove: `--hooks none --remove superpowers` deletes the hook, the copied script, and the manifest-listed skill dirs — skills it never managed are untouched.

Upstream: <https://github.com/obra/superpowers>

### OpenSpec

The installer updates the global CLI. OpenSpec also needs a per-project step: `openspec init` writes files into the working tree (`openspec/`, `.agents/skills/`), so it must be an explicit, consented action per repository — never something a session hook does implicitly.

Run it against the repo you want to enable:

```bash
# Initialize the target repo for Codex skill delivery
./install.sh --openspec-init

# Or point at another project; refreshes the integration if already initialized
./install.sh --openspec-init ../other-repo
```

Equivalent upstream command (tool names map to your selected harnesses):

```bash
cd your-project
openspec init --tools codex,opencode,oh-my-pi   # adjust to your harnesses
```

Current OpenSpec writes repo-local integrations per harness: `.agents/skills/openspec-*/` for Codex (invoked as `$openspec-<skill>`), `.opencode/commands/opsx-*.md` for OpenCode, and `.omp/commands/opsx-*.md` for oh-my-pi (invoked as `/opsx-*`). Names do not always match the canonical OPSX command: examples include `$openspec-propose`, `$openspec-apply-change`, `$openspec-sync-specs`, and `$openspec-archive-change`. The hook discovers the installed names instead of hard-coding them.

After upgrading the OpenSpec CLI, refresh each initialized project deliberately:

```bash
cd your-project
openspec update
```

This is not run automatically by the hook installer because OpenSpec updates may migrate generated files and remove obsolete managed integrations. Review each project and its version-control diff separately.

Upstream: <https://github.com/Fission-AI/OpenSpec>

## Configuration behavior

The generated Codex definitions use:

```json
{
  "matcher": "^(startup|clear|compact)$",
  "hooks": [{
    "type": "command",
    "command": "<quoted Python executable> \"$(git rev-parse --show-toplevel)/.agents/session-hooks/<script>\" --managed-by=codex-session-hooks",
    "commandWindows": "<Windows equivalent with absolute path>",
    "timeout": 15,
    "additionalContextLimit": 2500
  }]
}
```

The ownership marker allows safe idempotent upgrades and removal. Commands resolve from the git root so they keep working when Codex starts in a subdirectory; for `--repo` targets outside a git repo, an absolute-path command is written instead.

The OpenCode and oh-my-pi adapters are whole-file managed TypeScript identified by a `// managed-by: codex-session-hooks` header; each spawns the same Python handlers once per session and caches their stdout.

## Security model

- Hook output is instructions, not deterministic enforcement; model behavior can still vary.
- The OpenSpec hook never inserts change-directory names or artifact contents into developer context. Skill names must match `openspec-[a-z0-9-]+`.
- The Superpowers hook loads `using-superpowers/SKILL.md` from the repo's `.agents/skills/` (walking ancestors of the script location), falling back to `~/.agents/skills/` and `$CODEX_HOME/skills/`; `SUPERPOWERS_USING_SKILL` overrides everything.
- Both hooks cap their context and the Superpowers loader rejects unexpectedly large bootstrap files.
- Tarball extraction sanitizes member paths and skips non-file members — nothing escapes `.agents/skills/`.
- Hooks execute local scripts with your account's permissions. Review this repository before installation and before pulling updates.

## Verify

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile install.py superpowers-bootstrap.py openspec-context.py
bash -n install.sh session-start.sh openspec-detect.sh
python3 -m json.tool hooks.json >/dev/null
```

Runtime checks (from an installed repo):

```bash
cd your-project
python3 .agents/session-hooks/superpowers-bootstrap.py
python3 .agents/session-hooks/openspec-context.py
```

The OpenSpec command should print nothing outside an initialized project.

## License

MIT
