# Multi-harness install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native execution) to implement this plan task-by-task.

**Goal:** Install the session hooks into OpenCode and oh-my-pi alongside Codex, using each harness's official project-level discovery.

**Architecture:** Shared Python handlers move to `.agents/session-hooks/`; thin managed TS adapters (`.opencode/plugins/session-hooks.ts`, `.omp/extensions/session-hooks.ts`) spawn them once per session and inject stdout into the model context via each harness's official hook (`ctx.session.hook("context")` / `pi.on("context")` dedup rewrite). `--harness` selects targets; detected CLIs drive defaults.

**Tech Stack:** Python 3.10+ stdlib, generated TypeScript adapter sources, unittest.

**Spec:** `docs/superpowers/specs/2026-09-23-multi-harness-design.md`

## Global Constraints

- Python stdlib only; `from __future__ import annotations`; POSIX shell.
- Ownership: managed files marked (`--managed-by=codex-session-hooks` arg for Python; `// managed-by: codex-session-hooks` header for TS). Foreign files never touched.
- `.codex/hooks.json` writes stay atomic (temp + fsync + os.replace + `.bak`).
- Git-root hook commands `"$(git rev-parse --show-toplevel)/…"` — double-quoted.
- Nothing global except `codex features enable hooks`, only when codex selected.
- `openspec init` runs only via explicit `--openspec-init` or TUI confirm.
- Tests for every change; suite must stay green.

## Review Focus

1. `--harness` not passed on a codex-only machine → defaults to `{codex}` (backward compat).
2. `--harness opencode` without `opencode` binary → warns, still installs files.
3. Re-run after `--harness codex` on a repo that had opencode+omp adapters → managed adapters deleted, foreign plugin files preserved.
4. Adapter spawns a missing/failed python script → empty output, contributes nothing, no crash.
5. v2.0.0 install (`.codex/hooks/*.py`) upgraded → scripts moved, `.codex/hooks/` removed if empty, hooks.json commands regenerated.

---

### Task 1: `--harness` parsing + detection

**Files:**
- Modify: `install.py` (near `parse_hook_ids`, ~line 1031; `HOOK_DEFINITIONS` area ~line 60)
- Test: `tests/test_hooks.py`

**Interfaces:**
- Produces: `HARNESSES: dict[str, dict]` keyed by `codex|opencode|omp` with keys `cli`, `adapter_rel` (`None` for codex), `openspec_tool`. `parse_harness_ids(value: str) -> set[str]`, `detect_harnesses() -> set[str]`.

- [ ] **Step 1: Failing tests**

```python
def test_parse_harness_ids_valid(self):
    self.assertEqual(inst.parse_harness_ids("codex,omp"), {"codex", "omp"})
    self.assertEqual(inst.parse_harness_ids("all"), {"codex", "opencode", "omp"})
    self.assertEqual(inst.parse_harness_ids("none"), set())

def test_parse_harness_ids_invalid(self):
    with self.assertRaises(argparse.ArgumentTypeError):
        inst.parse_harness_ids("codex,bogus")
    with self.assertRaises(argparse.ArgumentTypeError):
        inst.parse_harness_ids("")

def test_detect_harnesses(self):
    with mock.patch.object(inst.shutil, "which", side_effect=lambda c: "/x" if c == "codex" else None):
        self.assertEqual(inst.detect_harnesses(), {"codex"})
```

- [ ] **Step 2: Run → fail** `python3 -m unittest tests.test_hooks -k harness` — ImportError/AttributeError.

- [ ] **Step 3: Implement**

```python
HARNESSES: dict[str, dict[str, object]] = {
    "codex":    {"cli": "codex",    "adapter_rel": None, "openspec_tool": "codex"},
    "opencode": {"cli": "opencode", "adapter_rel": ".opencode/plugins/session-hooks.ts", "openspec_tool": "opencode"},
    "omp":      {"cli": "omp",      "adapter_rel": ".omp/extensions/session-hooks.ts",   "openspec_tool": "oh-my-pi"},
}

def parse_harness_ids(value: str) -> set[str]:
    values = {item.strip() for item in value.split(",") if item.strip()}
    if values == {"all"}:
        return set(HARNESSES)
    if values == {"none"}:
        return set()
    unknown = values - set(HARNESSES)
    if not values or unknown:
        valid = ", ".join([*HARNESSES, "all", "none"])
        raise argparse.ArgumentTypeError(f"choose a comma-separated subset of: {valid}")
    return values

def detect_harnesses() -> set[str]:
    return {h for h, meta in HARNESSES.items() if shutil.which(str(meta["cli"]))}
```

- [ ] **Step 4: Tests pass** — same command.
- [ ] **Step 5: Commit** `feat: parse and detect --harness targets`

---

### Task 2: move handlers to `.agents/session-hooks/` + migration

**Files:**
- Modify: `install.py` — `install_hook_scripts` (~873), `remove_hook_scripts` (~885), `hook_handler` `script_rel` (~812)
- Test: `tests/test_hooks.py` — update paths + new migration test

**Interfaces:**
- Consumes: nothing new. Produces: `session_hooks_dir(repo_root) -> Path` returning `repo_root/".agents"/"session-hooks"`; `install_hook_scripts`/`remove_hook_scripts` operate there; `migrate_legacy_hook_dir(repo_root) -> None` removes managed scripts left under `.codex/hooks/` and the dir if empty.

- [ ] **Step 1: Update failing tests** — existing script-copy tests now expect `.agents/session-hooks/`; new test: create `.codex/hooks/superpowers-bootstrap.py` + a foreign `other.py`, run `migrate_legacy_hook_dir`, assert managed gone, foreign kept, dir still exists; then remove foreign, re-run → dir gone.

- [ ] **Step 2: Run → fail** on old `.codex/hooks` paths.

- [ ] **Step 3: Implement**

```python
def session_hooks_dir(repo_root: Path) -> Path:
    return repo_root / ".agents" / "session-hooks"

def install_hook_scripts(repo_root: Path, hook_ids: set[str]) -> None:
    dest_dir = session_hooks_dir(repo_root)
    source_dir = Path(__file__).resolve().parent
    if hook_ids:
        dest_dir.mkdir(parents=True, exist_ok=True)
    for hook_id in hook_ids:
        script = HOOK_DEFINITIONS[hook_id]["script"]
        shutil.copy2(source_dir / script, dest_dir / script)
    remove_hook_scripts(repo_root, set(HOOK_DEFINITIONS) - set(hook_ids))

def remove_hook_scripts(repo_root: Path, remove_ids: set[str]) -> None:
    dest_dir = session_hooks_dir(repo_root)
    for hook_id in remove_ids:
        target = dest_dir / HOOK_DEFINITIONS[hook_id]["script"]
        if target.is_file():
            target.unlink()
    if dest_dir.is_dir() and not any(dest_dir.iterdir()):
        dest_dir.rmdir()

def migrate_legacy_hook_dir(repo_root: Path) -> None:
    legacy = repo_root / ".codex" / "hooks"
    for definition in HOOK_DEFINITIONS.values():
        stale = legacy / definition["script"]
        if stale.is_file():
            stale.unlink()
    if legacy.is_dir() and not any(legacy.iterdir()):
        legacy.rmdir()
```

And in `hook_handler`: `script_rel = f".agents/session-hooks/{definition['script']}"`.

- [ ] **Step 4: Tests pass** — full suite.
- [ ] **Step 5: Commit** `feat: share hook handlers under .agents/session-hooks`

---

### Task 3: OpenCode adapter

**Files:**
- Modify: `install.py` — new section after `remove_hook_scripts`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `session_hooks_dir`, `HOOK_DEFINITIONS`, `HARNESSES["opencode"]["adapter_rel"]`.
- Produces: `write_opencode_adapter(repo_root, hook_ids) -> Path`, `remove_managed_adapter(path: Path) -> bool` (shared by Task 4: deletes file only if it contains `// managed-by: codex-session-hooks`).

- [ ] **Step 1: Failing tests**

```python
def test_opencode_adapter_written(self):
    root = self.make_repo()
    path = inst.write_opencode_adapter(root, {"superpowers", "openspec"})
    text = path.read_text()
    self.assertIn("// managed-by: codex-session-hooks", text)
    self.assertIn("superpowers-bootstrap.py", text)
    self.assertIn("openspec-context.py", text)
    self.assertIn('hook("context"', text)

def test_opencode_adapter_subset_and_removal(self):
    root = self.make_repo()
    path = inst.write_opencode_adapter(root, {"openspec"})
    self.assertNotIn("superpowers-bootstrap.py", path.read_text())
    foreign = path.with_name("other.ts"); foreign.write_text("export {}")
    self.assertTrue(inst.remove_managed_adapter(path))
    self.assertFalse(path.exists()); self.assertTrue(foreign.exists())
    self.assertFalse(inst.remove_managed_adapter(foreign))  # no marker → kept
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — template with script list baked in:

```python
MARKER_TS = "// managed-by: codex-session-hooks"

OPENCODE_ADAPTER_TEMPLATE = '''{marker}
import {{ Plugin }} from "@opencode/plugin"
import {{ spawnSync }} from "node:child_process"

const SCRIPTS = {scripts}

function runHandlers(dir: string): string[] {{
  const out: string[] = []
  for (const name of SCRIPTS) {{
    try {{
      const res = spawnSync("python3", [`${{dir}}/.agents/session-hooks/${{name}}`, "--managed-by=codex-session-hooks"], {{
        cwd: dir, encoding: "utf8", timeout: 15000,
      }})
      const text = (res.stdout ?? "").trim()
      if (res.status === 0 && text) out.push(text)
    }} catch {{}}
  }}
  return out
}}

export default Plugin.define({{
  id: "codex-session-hooks",
  async setup(ctx) {{
    const contexts = runHandlers(ctx.location.directory)
    if (!contexts.length) return
    await ctx.session.hook("context", (event) => {{
      for (const text of contexts) event.system.push({{ type: "text", text }})
    }})
  }},
}})
'''

def _adapter_scripts(hook_ids: set[str]) -> str:
    names = sorted(HOOK_DEFINITIONS[h]["script"] for h in hook_ids)
    return "[" + ", ".join(json.dumps(n) for n in names) + "]"

def write_opencode_adapter(repo_root: Path, hook_ids: set[str]) -> Path:
    rel = str(HARNESSES["opencode"]["adapter_rel"])
    path = repo_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OPENCODE_ADAPTER_TEMPLATE.format(
        marker=MARKER_TS, scripts=_adapter_scripts(hook_ids),
    ), encoding="utf-8")
    return path

def remove_managed_adapter(path: Path) -> bool:
    if not path.is_file() or MARKER_TS not in path.read_text(encoding="utf-8", errors="replace"):
        return False
    path.unlink()
    return True
```

- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit** `feat: generate OpenCode session-context plugin`

---

### Task 4: omp adapter

**Files:** same as Task 3.

**Interfaces:**
- Consumes: `_adapter_scripts`, `remove_managed_adapter`, `MARKER_TS`, `session_hooks_dir`.
- Produces: `write_omp_adapter(repo_root, hook_ids) -> Path`.

- [ ] **Step 1: Failing tests** — same shape: marker, script list, `pi.on("context"`, `customType` dedup; removal reuses `remove_managed_adapter`.

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement**

```python
OMP_ADAPTER_TEMPLATE = '''{marker}
import type {{ ExtensionAPI }} from "@oh-my-pi/pi-coding-agent";
import {{ spawnSync }} from "node:child_process";

const SCRIPTS = {scripts};
const CUSTOM_TYPE = "codex-session-hooks-context";
let cached: string[] | null = null;

function loadContexts(cwd: string): string[] {{
  const out: string[] = [];
  for (const name of SCRIPTS) {{
    try {{
      const res = spawnSync("python3", [`${{cwd}}/.agents/session-hooks/${{name}}`, "--managed-by=codex-session-hooks"], {{
        cwd, encoding: "utf8", timeout: 15000,
      }});
      const text = (res.stdout ?? "").trim();
      if (res.status === 0 && text) out.push(text);
    }} catch {{}}
  }}
  return out;
}}

export default function (pi: ExtensionAPI) {{
  pi.on("context", async (event, ctx) => {{
    if (cached === null) cached = loadContexts(ctx.cwd);
    if (!cached.length) return;
    const present = event.messages.some(
      (m: any) => m.role === "custom" && m.customType === CUSTOM_TYPE,
    );
    if (present) return;
    return {{
      messages: [
        {{
          role: "custom",
          customType: CUSTOM_TYPE,
          content: [{{ type: "text", text: cached.join("\\n\\n") }}],
          display: false,
        }},
        ...event.messages,
      ],
    }};
  }});
}}
'''

def write_omp_adapter(repo_root: Path, hook_ids: set[str]) -> Path:
    rel = str(HARNESSES["omp"]["adapter_rel"])
    path = repo_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OMP_ADAPTER_TEMPLATE.format(
        marker=MARKER_TS, scripts=_adapter_scripts(hook_ids),
    ), encoding="utf-8")
    return path
```

- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit** `feat: generate oh-my-pi session-context extension`

---

### Task 5: wire `main()` — flag, defaults, per-harness install/remove, openspec tools, gitignore

**Files:**
- Modify: `install.py` — argparse block, `main()` (~1183-1262), `ensure_gitignore`/`strip_gitignore` patterns, `init_openspec_project` call site
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: everything above. Produces: `--harness` flag; `select_harnesses(args, tty) -> set[str]`; `openspec_tools(harnesses) -> str`; gitignore entries per harness.

- [ ] **Step 1: Failing tests**

```python
def test_openspec_tools(self):
    self.assertEqual(inst.openspec_tools({"codex"}), "codex")
    self.assertEqual(inst.openspec_tools({"codex", "omp"}), "codex,oh-my-pi")

def test_deselected_harness_adapter_removed(self):
    root = self.make_repo()
    inst.write_omp_adapter(root, {"openspec"})
    inst.apply_harnesses(root, {"codex"}, {"openspec"}, set())  # omp not selected
    assert not (root / ".omp/extensions/session-hooks.ts").exists()
```

(Shape `apply_harnesses(repo_root, harnesses, hook_ids, remove_ids)` — writes codex hooks.json+scripts when `codex` in set, writes adapters for `opencode`/`omp`, removes managed adapters for non-selected, calls `migrate_legacy_hook_dir`.)

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement**

  - `parser.add_argument("--harness", type=parse_harness_ids, default=None, ...)`.
  - In `main()`: `harnesses = args.harness if args.harness is not None else (TUI multiselect over detected, or detected set with -y, or {"codex"} if none detected)`. Warn when selected harness's CLI missing from PATH (explicit flag only).
  - Replace the inline hook merge block with `apply_harnesses(...)`:
    - `migrate_legacy_hook_dir(repo_root)`
    - `install_hook_scripts(repo_root, hooks)` when hooks non-empty (shared dir serves all harnesses)
    - codex: `enable_codex_hooks(codex_home)` + `merge_hooks` + `write_hooks_atomic` — only when `"codex" in harnesses`
    - opencode/omp: `write_*_adapter(repo_root, hooks)`; for each non-selected harness with `adapter_rel`, `remove_managed_adapter(repo_root / rel)`
  - `openspec init`: `init_openspec_project(target, openspec_tools(harnesses))` — pass joined tools (update `init_openspec_project` signature to accept tools list).
  - gitignore: managed block lines become per-harness — `/.codex/hooks.json`, `/.codex/hooks/` only when codex; `/.agents/session-hooks/`, `/.agents/skills/...` always; `/.opencode/plugins/session-hooks.ts`, `/.omp/extensions/session-hooks.ts` per harness.
  - `codex features enable hooks` runs only when `"codex" in harnesses`.

- [ ] **Step 4: Full suite green** + `py_compile`.
- [ ] **Step 5: Commit** `feat: install session context across codex, opencode, and omp`

---

### Task 6: openspec-context.py opsx-command awareness

**Files:**
- Modify: `openspec-context.py` — after skill discovery
- Test: `tests/test_hooks.py`

**Interfaces:** unchanged signature; output gains a line listing `opsx-*` commands found in `.opencode/commands/` or `.omp/commands/` at the project root.

- [ ] **Step 1:** failing test — repo with `openspec/config.yaml` + `.omp/commands/opsx-propose.md` → output mentions `opsx-propose`.
- [ ] **Step 2–4:** implement scan of those two dirs (non-recursive `opsx-*.md`), emit one line; green.
- [ ] **Step 5: Commit** `feat: report opsx commands in openspec context`

---

### Task 7: docs, version 2.1.0, real-machine test

- `VERSION = "2.1.0"`; README/AGENTS/memory updates (`--harness`, new layout, per-harness mechanisms, opsx commands); `decisions.md` entry 0011.
- Real-machine: scratch repo → `install.py -y --harness all`; verify all files; run `opencode`/`omp` headless smoke check that adapters load and context reaches the session; run unit suite.
- Commit `docs: multi-harness docs and v2.1.0 bump`.
