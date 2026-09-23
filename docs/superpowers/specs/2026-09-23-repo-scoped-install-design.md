# Repo-scoped install — design spec

> Status: draft, awaiting review. Target release: v2.0.0 (breaking CLI change).

## Context and motivation

Today the installer registers both SessionStart hooks in the **user-global**
`~/.codex/hooks.json`, pointing at scripts under a persistent managed dir
(`~/.local/share/codex-session-hooks`). Superpowers content is resolved from the
global `codex plugin` installation, and OpenSpec context is emitted whenever the
cwd happens to be inside an OpenSpec project.

The user wants installation to be **repo/project-scoped**: nothing is installed
globally, every artifact lives inside the target repository, and skills are
materialized from upstream sources into the repo. Where an official documented
mechanism exists it must be used; custom code is acceptable only for the gaps.

## Official mechanisms this design relies on

Verified against https://developers.openai.com/codex/hooks and
https://developers.openai.com/codex/skills (Codex CLI 0.156.1):

- **Project hooks** — `<repo>/.codex/hooks.json` (or inline `[hooks]` in
  `.codex/config.toml`) is an official hook source. It loads only when the
  project `.codex/` layer is trusted; each hook definition still needs `/hooks`
  approval. Docs recommend `$(git rev-parse --show-toplevel)`-based commands so
  hooks resolve correctly when Codex starts in a subdirectory.
- **Repo skills** — Codex auto-discovers `.agents/skills/` at the repo root and
  every ancestor of the cwd up to it (REPO scope). Symlinked skill folders are
  followed. No hook is needed for skills to appear in the selector.
- **OpenSpec** — `openspec init --tools codex` already writes
  `.agents/skills/openspec-*` + `openspec/config.yaml` into the project
  (verified on openspec 1.13.1). Already repo-scoped; only the CLI binary is a
  global prerequisite tool (like `node` itself).
- **No official repo-scoped plugin install** — `codex plugin add` always writes
  to the user-level plugin cache. This is the single gap requiring custom
  implementation: materializing Superpowers skills into `.agents/skills/`.

Known upstream caveats (document, do not work around):

- Project hooks are silently ignored inside git worktrees (openai/codex#27133).
- The first SessionStart in a repo fires before hooks are trusted, so it is
  skipped; subsequent sessions work after `/hooks` approval
  (openai/codex#35306).

## Goals

- `install.py` installs hooks and skills **into a repository** — nothing is
  written to `~/.codex/hooks.json` or a persistent managed dir.
- The installer always states the resolved target repo and asks for
  confirmation before writing (non-interactive mode proceeds with the resolved
  target).
- Superpowers skills are fetched from upstream (`obra/superpowers` GitHub
  release tarball) into `<repo>/.agents/skills/`, pinned and recorded in a
  manifest for clean update/removal.
- Repo is self-contained after install: hook scripts are copied into
  `<repo>/.codex/hooks/` so hook commands never point outside the repo.
- Existing invariants preserved: `--managed-by` ownership marker, atomic
  hooks.json writes with timestamped backup, foreign hooks preserved
  byte-for-byte, hooks never write files, graceful degradation.
- One-time migration: existing managed entries in `~/.codex/hooks.json` are
  detected and offered for removal (foreign hooks preserved).

## Non-goals

- Per-project plugin installs (impossible — Codex plugins are user-global by
  design).
- Repo-scoped OpenSpec CLI install (the CLI is a toolchain prerequisite like
  node; only its generated skills/config are repo artifacts).
- Committing generated files — default is local (gitignored) materialization;
  the TUI offers a choice. (See "Tracking policy".)
- Windows-native hooks (`commandWindows` still emitted; same unverified status
  as today).

## Design

### Target resolution and confirmation

Order of precedence for the target repo root:

1. `--repo DIR` explicit flag.
2. `git rev-parse --show-toplevel` from the installer cwd.
3. Interactive TUI prompt for a directory (only when a tty exists).
4. Hard error in non-interactive mode.

Before any write, the installer displays the resolved root and asks
"Install SessionStart hooks and skills into <root>?" (TUI confirm; skipped by
`-y`). A non-git target is rejected unless `--repo` was given, in which case
hook commands fall back to absolute paths with a warning that moving the repo
breaks them (git-root form is preferred — it survives subdirectory starts).

### Repo file layout

```
<repo>/
├── .codex/
│   ├── hooks.json                     # merged, atomic write + .bak timestamp
│   └── hooks/
│       ├── superpowers-bootstrap.py   # copied from installer dir
│       └── openspec-context.py        # copied from installer dir
├── .agents/skills/
│   ├── using-superpowers/             # materialized from upstream tarball
│   ├── brainstorming/                 #   (all skills under upstream skills/)
│   ├── …                              #
│   ├── openspec-*/                    # generated by `openspec init --tools codex`
│   └── .codex-session-hooks.json      # manifest (see below)
└── .gitignore                         # managed block added when "local" chosen
```

The manifest `.agents/skills/.codex-session-hooks.json` records what we own:

```json
{
  "managed-by": "codex-session-hooks",
  "superpowers": {
    "ref": "v6.4.1",
    "skills": ["brainstorming", "using-superpowers", "..."]
  }
}
```

On update: download the tarball for the target ref, compute the new skill set,
delete manifest-listed dirs that disappeared, extract the rest, rewrite the
manifest. On `--remove`: delete manifest-listed dirs and the manifest. Skill
dirs not in the manifest are never touched (user skills, openspec-* skills).
Exception on first materialization: if a target skill dir already exists and
matches an upstream skill name, it is replaced and adopted into the manifest —
a name collision means it is already a copy of the same upstream skill.

### Hook registration

Same merge machinery (`merge_hooks`, `managed_hook_id`, `write_hooks_atomic`,
`load_hooks`), now targeting `<repo>/.codex/hooks.json`. `hook_handler` gains a
repo variant emitting the official git-root form:

```
python3 "$(git rev-parse --show-toplevel)/.codex/hooks/superpowers-bootstrap.py" --managed-by=codex-session-hooks
```

`commandWindows` keeps the absolute-path form (git rev-parse is not available
in cmd.exe). Matcher stays `^(startup|clear|compact)$`. Ownership detection is
unchanged: `--managed-by=codex-session-hooks` token + script basename, plus the
legacy statusMessage/script-name fallback for migrating old installs.

### Hook script changes

`superpowers-bootstrap.py` resolution order becomes:

1. `SUPERPOWERS_USING_SKILL` env override (unchanged).
2. `<script_dir>/../../.agents/skills/using-superpowers/SKILL.md` — i.e. the
   repo's materialized copy, resolved from `Path(__file__).resolve()`
   (script lives in `<repo>/.codex/hooks/`).
3. `~/.agents/skills/using-superpowers/SKILL.md` (official USER scope).
4. `$CODEX_HOME/skills/using-superpowers/SKILL.md` (legacy fallback).

The `codex plugin list --json` subprocess call is dropped from the hot path —
repo installs no longer depend on a global plugin being present, and the hook
gets faster (no up-to-10s subprocess). `MAX_SKILL_CHARS` cap and the
`<superpowers-bootstrap>` envelope stay.

`openspec-context.py` is unchanged: ancestor search for
`openspec/config.yaml`, `openspec-*` skill discovery under `.agents/skills` /
`.codex/skills`, CLI-missing warning, precedence rule. Silent outside OpenSpec
projects.

### Superpowers materialization

New `materialize_superpowers(repo_root, ref=None)`:

- Resolve target ref: `--superpowers-ref` flag, else latest GitHub release tag
  for `obra/superpowers` (generalize `latest_release_tag` to take a repo).
- Download `https://github.com/obra/superpowers/archive/<ref>.tar.gz` via
  `urllib` (stdlib; keeps zero-dependency requirement).
- Extract with `tarfile` (stdlib), restricted to `*/skills/**` members with
  sanitized relative paths (reject `..`/absolute — pre-3.12 has no
  `filter="data"`).
- Sync each `skills/<name>` dir into `<repo>/.agents/skills/<name>` per the
  manifest rules above. Verify `using-superpowers/SKILL.md` exists after sync;
  error otherwise.
- Write the manifest with the resolved ref.

This replaces `update_superpowers` (the `codex plugin` path is deleted with
global mode).

### OpenSpec integration

Unchanged official flow: `update_openspec` keeps the CLI current via the
detected package manager; `--openspec-init [DIR]` / TUI prompt run
`openspec init --tools codex` inside the target repo (writes
`.agents/skills/openspec-*` + `openspec/config.yaml`). The openspec-context
hook still fires only inside OpenSpec projects.

### Global cleanup / migration

On every run, inspect `~/.codex/hooks.json` for `managed_hook_id` hits. If any
exist, prompt: "Managed hooks found in the global config — remove them?" (TUI
confirm; `-y` removes). Removal reuses `merge_hooks` with empty install set and
the remove set; foreign hooks and backups behave exactly as today.

`[features] hooks = true` in `~/.codex/config.toml` is still ensured globally —
it is a user capability flag, not a repo artifact. Project-layer trust and
per-hook approval remain user actions (`/hooks`); the installer never writes
trust state.

### install.sh

Piped mode changes from persistent to transient: download the repo tarball to
`mktemp -d`, run `install.py` against the resolved target, let the temp dir be
cleaned by the trap. `CODEX_HOOKS_HOME` is dropped; `CODEX_HOOKS_REF` /
`CODEX_HOOKS_SOURCE_URL` stay. In-clone mode unchanged (run in place; target =
resolved repo, which may differ from the clone's own location).

### Tracking policy

Default: generated files are **local** — installer appends a managed block to
`.gitignore` covering `.codex/hooks.json`, `.codex/hooks/`, and each
manifest-listed skill dir (generated from the manifest, so it stays accurate
across updates). TUI offers "Commit hooks and skills to the repo?" — if chosen,
no gitignore entries are added for them. OpenSpec-generated skills follow
whatever `.gitignore` policy the user picks for `.agents/skills` entries (same
choice prompt).

### CLI surface changes (v2.0.0)

- New: `--repo DIR`, `--superpowers-ref REF`.
- Removed: global install scope (no replacement flag — repo is the only mode).
- Kept: `--hooks`, `--remove`, `-y`, `--skip-update-check`,
  `--skip-framework-updates`, `--openspec-package-manager`, `--codex-home`
  (now only for `config.toml`/global-cleanup reads), `--openspec-init`.

### Error handling

- Non-git cwd without `--repo` and no tty → `InstallError` with clear message.
- Tarball download/extract failures → `InstallError`; partial extraction cleans
  up staged files before writing (extract to temp dir, then sync).
- Missing `using-superpowers` after materialization → `InstallError`.
- Invalid repo `.codex/hooks.json` → existing `load_hooks` validation.

### Testing

Update `tests/test_hooks.py` (load-by-path pattern stays):

- `merge_hooks` repo-mode: git-root command strings, marker detection,
  foreign-hook preservation, remove path.
- Manifest round-trip and stale-skill cleanup in `materialize_superpowers`
  (fixture tarball via `tarfile` in-memory).
- `superpowers-bootstrap.py` resolution order (repo copy > env > fallbacks).
- `.gitignore` block generation idempotency.
- Global cleanup: managed entries stripped, `herdr`-style foreign hooks kept.
- `install.sh` syntax check stays in the suite doc.

### Docs

Update `AGENTS.md` (overview, layout, invariants — the "absolute paths in hook
commands" invariant becomes "git-root-relative commands"), `README.md`, and
`docs/memory/` (`current-state.md` rewrite + `decisions.md` append) in the same
commit. Release notes mention the v2.0.0 break and the migration prompt.
