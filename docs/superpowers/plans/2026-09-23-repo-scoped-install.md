# Repo-Scoped Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the installer from global (`~/.codex/hooks.json` + managed dir) to repo-scoped: hooks live in `<repo>/.codex/hooks.json`, scripts in `<repo>/.codex/hooks/`, and Superpowers skills materialize from an upstream tarball into `<repo>/.agents/skills/`.

**Architecture:** Everything writes inside the resolved repo root. Hook commands use the official `$(git rev-parse --show-toplevel)` form. Superpowers skills come from `obra/superpowers` release tarballs (stdlib `urllib` + `tarfile`), tracked by a manifest for clean update/remove. A migration pass strips managed entries from `~/.codex/hooks.json`.

**Tech Stack:** Python 3.10+ stdlib only, POSIX bash, unittest.

**Spec:** `docs/superpowers/specs/2026-09-23-repo-scoped-install-design.md`

## Global Constraints

- Python 3.10+, `from __future__ import annotations`, stdlib only — no third-party deps.
- Ownership marker stays `--managed-by=codex-session-hooks` in hook commands.
- Atomic hooks.json writes via `write_hooks_atomic` (temp + `os.replace` + `.bak`).
- Foreign hooks preserved byte-for-byte; `managed_hook_id` logic unchanged.
- Matcher stays `^(startup|clear|compact)$`; timeout 15; `additionalContextLimit` 2500.
- Repo hook command form: `<python> "$(git rev-parse --show-toplevel)/.codex/hooks/<script>" --managed-by=codex-session-hooks` — the `$(...)` must be **double**-quoted (single quotes kill substitution).
- Hook scripts never write files; print context or nothing.
- Git identity for commits: `git -c user.name="Lenin AGC" -c user.email="934933+IGUNUBLUE@users.noreply.github.com"`. No attribution trailers.
- Full suite before every commit-worthy milestone:
  `python3 -m unittest discover -s tests -v && python3 -m py_compile install.py superpowers-bootstrap.py openspec-context.py && bash -n install.sh session-start.sh openspec-detect.sh && python3 -m json.tool hooks.json >/dev/null`

## Review Focus

- `$(git rev-parse …)` single-quoted by `shlex.join` would silently produce literal `$(…)` paths → test pins double-quoted form (Task 2).
- Tarball path traversal (`../evil`) and non-file members (symlinks escaping the dest) → sanitization test (Task 3).
- Pre-existing `.agents/skills/using-superpowers` not in the manifest on first run → replaced + adopted (spec exception) → test (Task 3).
- `.gitignore` block: idempotent re-runs, user content outside markers preserved → tests (Task 4).
- Global cleanup on missing/corrupt `~/.codex/hooks.json` and on foreign-only files → no-op, no prompt → tests (Task 5).
- `merge_hooks` signature change ripples into every existing call site and test → updated in Task 2.

---

### Task 1: `superpowers-bootstrap.py` repo-first resolution

**Files:**
- Modify: `superpowers-bootstrap.py`
- Test: `tests/test_hooks.py` (`SuperpowersTests`)

**Interfaces:**
- Produces: `find_superpowers_skill(codex_home=None, environ=None, script_dir=None) -> Path | None` — `script_dir` defaults to `Path(__file__).resolve().parent`; ancestors of it are searched for `.agents/skills/using-superpowers/SKILL.md`.
- Consumes: nothing new.

- [ ] **Step 1: Rewrite the failing tests**

Replace the two plugin-resolution tests in `SuperpowersTests` with:

```python
def test_repo_skill_found_via_script_ancestors(self):
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        hooks_dir = repo / ".codex" / "hooks"
        skills = repo / ".agents" / "skills" / "using-superpowers"
        hooks_dir.mkdir(parents=True)
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("repo skill")
        (hooks_dir / "superpowers-bootstrap.py").write_text("")

        found = superpowers.find_superpowers_skill(
            codex_home=repo / "nope",
            environ={"HOME": str(repo / "home"), "PATH": "/usr/bin"},
            script_dir=hooks_dir,
        )
        self.assertEqual(found, skills / "SKILL.md")

def test_env_override_and_missing(self):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        override = root / "custom" / "SKILL.md"
        override.parent.mkdir(parents=True)
        override.write_text("x")
        env = {"HOME": str(root / "home"), "PATH": "/usr/bin",
               "SUPERPOWERS_USING_SKILL": str(override)}
        self.assertEqual(
            superpowers.find_superpowers_skill(root / "ch", env, root), override)
        env2 = {"HOME": str(root / "home"), "PATH": "/usr/bin"}
        self.assertIsNone(
            superpowers.find_superpowers_skill(root / "ch", env2, root))

def test_user_scope_fallback(self):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        user = root / "home" / ".agents" / "skills" / "using-superpowers"
        user.mkdir(parents=True)
        (user / "SKILL.md").write_text("user skill")
        found = superpowers.find_superpowers_skill(
            root / "ch", {"HOME": str(root / "home"), "PATH": "/usr/bin"},
            root / "elsewhere")
        self.assertEqual(found, user / "SKILL.md")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_hooks.SuperpowersTests -v`
Expected: FAIL — `find_superpowers_skill` has no `script_dir` param.

- [ ] **Step 3: Implement**

In `superpowers-bootstrap.py`:

```python
def _ancestors(start: Path):
    current = start.resolve()
    yield current
    yield from current.parents


def find_superpowers_skill(
    codex_home: Path | None = None,
    environ: dict[str, str] | None = None,
    script_dir: Path | None = None,
) -> Path | None:
    env = os.environ if environ is None else environ
    home = Path(env.get("HOME", str(Path.home())))
    codex_home = codex_home or Path(env.get("CODEX_HOME", home / ".codex"))
    script_dir = script_dir or Path(__file__).resolve().parent

    override = env.get("SUPERPOWERS_USING_SKILL")
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() else None

    candidates = [
        ancestor / ".agents" / "skills" / "using-superpowers" / "SKILL.md"
        for ancestor in _ancestors(script_dir)
    ]
    candidates += [
        home / ".agents" / "skills" / "using-superpowers" / "SKILL.md",
        codex_home / "skills" / "using-superpowers" / "SKILL.md",
    ]
    return next((c for c in candidates if c.is_file()), None)
```

Delete `_active_superpowers_entry` and the now-unused `shutil`, `subprocess` imports. (`json` stays for `render_bootstrap`.)

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_hooks.SuperpowersTests -v`
Expected: PASS (3 new + render test).

- [ ] **Step 5: Commit**

```bash
git -c user.name="Lenin AGC" -c user.email="934933+IGUNUBLUE@users.noreply.github.com" \
  commit -am "feat: resolve superpowers skill from repo .agents/skills"
```

---

### Task 2: Repo target resolution, repo-form `hook_handler`, script copying

**Files:**
- Modify: `install.py`
- Test: `tests/test_hooks.py` (`InstallerTests`)

**Interfaces:**
- Produces:
  - `resolve_repo_root(repo_arg: Path | None, tty, yes: bool) -> tuple[Path, bool]` — returns `(root, git_rooted)`.
  - `hook_handler(repo_root: Path, hook_id: str, git_rooted: bool = True) -> dict`
  - `merge_hooks(data, repo_root, install_ids, remove_ids=None, git_rooted=True)` — `script_dir` param renamed/relocated; all call sites updated.
  - `install_hook_scripts(repo_root: Path, hook_ids: set[str]) -> None` — copies scripts to `<repo>/.codex/hooks/`, removes scripts of managed hooks not in `hook_ids`.
  - `remove_hook_scripts(repo_root: Path, remove_ids: set[str]) -> None`
- Consumes: Task 1's module unchanged.

- [ ] **Step 1: Failing tests**

```python
def test_repo_hook_command_uses_git_root(self):
    handler = installer.hook_handler(Path("/repo"), "superpowers", git_rooted=True)
    self.assertIn('"$(git rev-parse --show-toplevel)/.codex/hooks/', handler["command"])
    self.assertIn(installer.MARKER, handler["command"])
    self.assertNotIn("'", handler["command"].split("git rev-parse")[1])  # no single quotes around $(...)

def test_non_git_repo_falls_back_to_absolute(self):
    handler = installer.hook_handler(Path("/repo"), "openspec", git_rooted=False)
    self.assertIn("/repo/.codex/hooks/openspec-context.py", handler["command"])
    self.assertNotIn("git rev-parse", handler["command"])

def test_install_and_remove_hook_scripts(self):
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        installer.install_hook_scripts(repo, {"superpowers", "openspec"})
        hooks_dir = repo / ".codex" / "hooks"
        self.assertTrue((hooks_dir / "superpowers-bootstrap.py").is_file())
        self.assertTrue((hooks_dir / "openspec-context.py").is_file())
        installer.remove_hook_scripts(repo, {"openspec"})
        self.assertFalse((hooks_dir / "openspec-context.py").exists())
        self.assertTrue((hooks_dir / "superpowers-bootstrap.py").is_file())

def test_resolve_repo_root_explicit_and_errors(self):
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        root, git_rooted = installer.resolve_repo_root(repo, None, True)
        self.assertEqual(root, repo.resolve())
        self.assertFalse(git_rooted)  # tmp dir is not a git repo
        with self.assertRaises(installer.InstallError):
            installer.resolve_repo_root(repo / "missing", None, True)
```

Update existing tests: `merge_hooks(data, ROOT, …)` → `merge_hooks(data, ROOT, …, git_rooted=True)`; `test_paths_with_spaces_are_shell_quoted` → assert on `commandWindows` (absolute path quoting) or the non-git path; `test_discovers_superpowers_only_from_official_marketplace` is deleted in Task 6 with the plugin code — mark it for removal now (it will keep failing until then; that's fine, or delete it in this task and let Task 6 remove the impl — simpler: delete in Task 6 along with the functions).

For git detection in `resolve_repo_root`, mock `shutil.which`/`_probe_output` in the explicit-arg test by passing `repo` arg (no git call needed since `--repo` path checks `git -C root rev-parse` → mock `installer._probe_output` return None for non-git, or a path for git). Write the test so `--repo` still detects git when present:

```python
def test_resolve_repo_root_detects_git(self):
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        with mock.patch.object(installer, "_probe_output", return_value=str(repo)):
            root, git_rooted = installer.resolve_repo_root(repo, None, True)
        self.assertTrue(git_rooted)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_hooks.InstallerTests -v`
Expected: FAIL — new names don't exist.

- [ ] **Step 3: Implement**

In `install.py`, replace `hook_handler` and adjust `merge_hooks`:

```python
def hook_handler(repo_root: Path, hook_id: str, git_rooted: bool = True) -> dict[str, object]:
    definition = HOOK_DEFINITIONS[hook_id]
    script_rel = f".codex/hooks/{definition['script']}"
    script_abs = (repo_root / script_rel).resolve()
    if git_rooted:
        command = (
            f'{shlex.quote(sys.executable)} '
            f'"$(git rev-parse --show-toplevel)/{script_rel}" {MARKER}'
        )
    else:
        command = shlex.join([sys.executable, str(script_abs), MARKER])
    return {
        "type": "command",
        "command": command,
        "commandWindows": subprocess.list2cmdline(
            [sys.executable, str(script_abs), MARKER]
        ),
        "statusMessage": definition["status"],
        "timeout": 15,
        "additionalContextLimit": 2500,
    }


def merge_hooks(data, repo_root, install_ids, remove_ids=None, git_rooted=True):
    # identical body; hook_handler(repo_root, hook_id, git_rooted)
```

```python
def resolve_repo_root(repo_arg: Path | None, tty, yes: bool) -> tuple[Path, bool]:
    if repo_arg is not None:
        root = repo_arg.expanduser().resolve()
        if not root.is_dir():
            raise InstallError(f"--repo target is not a directory: {root}")
    else:
        probe = _probe_output(["git", "rev-parse", "--show-toplevel"]) if shutil.which("git") else None
        if probe:
            return Path(probe.strip()).resolve(), True
        if tty is not None and not yes:
            entered = prompt_text(tty, "Repository directory to install into")
            if entered:
                root = Path(entered).expanduser().resolve()
                if not root.is_dir():
                    raise InstallError(f"not a directory: {root}")
            else:
                raise InstallError("no repository given")
        else:
            raise InstallError(
                "not inside a git repository; rerun inside one or pass --repo DIR"
            )
    inside = _probe_output(["git", "-C", str(root), "rev-parse", "--show-toplevel"])
    git_rooted = bool(inside) and Path(inside.strip()).resolve() == root
    return root, git_rooted
```

(`--repo` on a subdirectory of a repo: `inside` returns the toplevel — if it differs from `root`, we still treat `root` as install target but `git_rooted=False`? No — spec wants git-root commands only when the target IS the toplevel. If `inside` resolves to an ancestor ≠ root, git-root commands would point at the ancestor's `.codex/hooks` — wrong. So `git_rooted` requires equality, as coded.)

```python
def prompt_text(tty, question: str) -> str:
    tty.write(f"{_bar()} {question}: ")
    tty.flush()
    line = tty.readline()
    if not line:
        raise InstallError("input closed")
    return line.strip()


def install_hook_scripts(repo_root: Path, hook_ids: set[str]) -> None:
    dest_dir = repo_root / ".codex" / "hooks"
    dest_dir.mkdir(parents=True, exist_ok=True)
    source_dir = Path(__file__).resolve().parent
    for hook_id in hook_ids:
        shutil.copy2(source_dir / HOOK_DEFINITIONS[hook_id]["script"],
                     dest_dir / HOOK_DEFINITIONS[hook_id]["script"])
    remove_hook_scripts(repo_root, set(HOOK_DEFINITIONS) - set(hook_ids))


def remove_hook_scripts(repo_root: Path, remove_ids: set[str]) -> None:
    for hook_id in remove_ids:
        stale = repo_root / ".codex" / "hooks" / HOOK_DEFINITIONS[hook_id]["script"]
        if stale.is_file():
            stale.unlink()
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_hooks.InstallerTests -v`
Expected: PASS (except the marketplace test slated for Task 6 deletion — keep it green by leaving `_official_superpowers_entry` in place until then).

- [ ] **Step 5: Commit**

```bash
git -c user.name="Lenin AGC" -c user.email="934933+IGUNUBLUE@users.noreply.github.com" \
  commit -am "feat: repo-scoped hook registration and script install"
```

---

### Task 3: Superpowers materialization (tarball → `.agents/skills`) + manifest

**Files:**
- Modify: `install.py`
- Test: `tests/test_hooks.py` (new `MaterializeTests`)

**Interfaces:**
- Produces:
  - `SUPERPOWERS_REPO = "obra/superpowers"`, `MANIFEST_NAME = ".codex-session-hooks.json"`
  - `skills_dir(repo_root) -> Path` (`<repo>/.agents/skills`)
  - `load_manifest(repo_root) -> dict`, `save_manifest(repo_root, data) -> None`
  - `latest_release_tag(repo: str = REPO, timeout: int = 8) -> str | None` (generalized)
  - `materialize_superpowers(repo_root: Path, ref: str | None = None) -> str` — returns installed ref
  - `remove_superpowers(repo_root: Path) -> bool`
- Consumes: `run_command` not needed; uses `urllib.request`, `tarfile`, `io`, `tempfile`.

- [ ] **Step 1: Failing tests**

```python
class MaterializeTests(unittest.TestCase):
    def _tarball(self, members):
        import io, tarfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                payload = data.encode()
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        return buf.getvalue()

    def test_materialize_extracts_skills_and_writes_manifest(self):
        payload = self._tarball({
            "superpowers-1/skills/using-superpowers/SKILL.md": "bootstrap",
            "superpowers-1/skills/brainstorming/SKILL.md": "brainstorm",
            "superpowers-1/README.md": "ignored",
        })
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            with mock.patch.object(installer, "_download", return_value=payload):
                ref = installer.materialize_superpowers(repo, ref="v1.0.0")
            self.assertEqual(ref, "v1.0.0")
            skill = repo / ".agents/skills/using-superpowers/SKILL.md"
            self.assertEqual(skill.read_text(), "bootstrap")
            self.assertFalse((repo / ".agents/skills/README.md").exists())
            manifest = installer.load_manifest(repo)
            self.assertEqual(manifest["superpowers"]["ref"], "v1.0.0")
            self.assertIn("brainstorming", manifest["superpowers"]["skills"])

    def test_rejects_path_traversal(self):
        payload = self._tarball({
            "superpowers-1/skills/using-superpowers/SKILL.md": "ok",
            "superpowers-1/skills/../../evil.txt": "nope",
        })
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            with mock.patch.object(installer, "_download", return_value=payload):
                installer.materialize_superpowers(repo, ref="v1.0.0")
            self.assertFalse((repo / "evil.txt").exists())
            self.assertFalse((repo / ".agents/evil.txt").exists())

    def test_missing_using_superpowers_fails(self):
        payload = self._tarball({
            "superpowers-1/skills/other/SKILL.md": "x",
        })
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(installer, "_download", return_value=payload):
                with self.assertRaises(installer.InstallError):
                    installer.materialize_superpowers(Path(directory), ref="v1.0.0")

    def test_update_removes_stale_and_adopts_preexisting(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            first = self._tarball({
                "sp/skills/using-superpowers/SKILL.md": "a",
                "sp/skills/old-skill/SKILL.md": "old",
            })
            second = self._tarball({
                "sp/skills/using-superpowers/SKILL.md": "b",
            })
            with mock.patch.object(installer, "_download", return_value=first):
                installer.materialize_superpowers(repo, ref="v1")
            self.assertTrue((repo / ".agents/skills/old-skill").is_dir())
            with mock.patch.object(installer, "_download", return_value=second):
                installer.materialize_superpowers(repo, ref="v2")
            self.assertFalse((repo / ".agents/skills/old-skill").exists())
            self.assertEqual(
                (repo / ".agents/skills/using-superpowers/SKILL.md").read_text(), "b")
            # remove
            self.assertTrue(installer.remove_superpowers(repo))
            self.assertFalse((repo / ".agents/skills/using-superpowers").exists())
            self.assertFalse(
                (repo / ".agents/skills/.codex-session-hooks.json").exists())
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_hooks.MaterializeTests -v`
Expected: FAIL — names missing.

- [ ] **Step 3: Implement**

In `install.py` (imports add `io`, `tarfile`, `urllib.request` already imported for `latest_release_tag`):

```python
SUPERPOWERS_REPO = "obra/superpowers"
MANIFEST_NAME = ".codex-session-hooks.json"


def skills_dir(repo_root: Path) -> Path:
    return repo_root / ".agents" / "skills"


def load_manifest(repo_root: Path) -> dict:
    path = skills_dir(repo_root) / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_manifest(repo_root: Path, data: dict) -> None:
    path = skills_dir(repo_root) / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _download(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": PROJECT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _extract_skills(payload: bytes, staging: Path) -> list[str]:
    """Extract <top>/skills/<name>/** members into staging/<name>; returns names."""
    names: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = PurePosixPath(member.name).parts
            if len(parts) < 3 or parts[1] != "skills" or ".." in parts:
                continue
            if not (member.isfile() or member.isdir()):
                continue  # skip links/devices: nothing escapes staging
            rel = Path(*parts[2:])  # <name>/...
            target = staging / rel
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is not None:
                with extracted as src:
                    target.write_bytes(src.read())
            names.add(parts[2])
    return sorted(names)
```

```python
def materialize_superpowers(repo_root: Path, ref: str | None = None) -> str:
    ref = ref or latest_release_tag(SUPERPOWERS_REPO)
    if not ref:
        raise InstallError(
            f"could not resolve latest {SUPERPOWERS_REPO} release; pass --superpowers-ref"
        )
    url = f"https://github.com/{SUPERPOWERS_REPO}/archive/{ref}.tar.gz"
    try:
        payload = _download(url)
    except (OSError, urllib.error.URLError) as error:
        raise InstallError(f"failed to download {url}: {error}") from error

    dest = skills_dir(repo_root)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        names = _extract_skills(payload, staging)
        if not (staging / "using-superpowers" / "SKILL.md").is_file():
            raise InstallError(
                f"{ref} tarball did not contain using-superpowers/SKILL.md"
            )
        dest.mkdir(parents=True, exist_ok=True)
        for name in names:  # sync staging -> dest; adopts pre-existing same-name dirs
            target = dest / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(staging / name, target)

    previous = set(load_manifest(repo_root).get("superpowers", {}).get("skills", []))
    for stale in previous - set(names):
        target = dest / stale
        if target.is_dir():
            shutil.rmtree(target)
    manifest = load_manifest(repo_root)
    manifest["managed-by"] = PROJECT
    manifest["superpowers"] = {"ref": ref, "skills": names}
    save_manifest(repo_root, manifest)
    print(f"Superpowers {ref} materialized into {dest} ({len(names)} skills).")
    return ref


def remove_superpowers(repo_root: Path) -> bool:
    manifest = load_manifest(repo_root)
    names = manifest.get("superpowers", {}).get("skills", [])
    removed = False
    for name in names:
        target = skills_dir(repo_root) / name
        if target.is_dir():
            shutil.rmtree(target)
            removed = True
    manifest_path = skills_dir(repo_root) / MANIFEST_NAME
    if manifest_path.is_file():
        manifest_path.unlink()
        removed = True
    return removed
```

And generalize `latest_release_tag(tag_url_repo)`:

```python
def latest_release_tag(repo: str = REPO, timeout: int = 8) -> str | None:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": PROJECT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    tag = data.get("tag_name")
    return tag if isinstance(tag, str) and tag else None
```

Check current `latest_release_tag` body (line ~394) and adapt — keep behavior identical, just parameterized repo.

Add `from pathlib import PurePosixPath`? Already `from pathlib import Path` — add `PurePosixPath` to that import. `import io`, `import tarfile` top-level.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_hooks.MaterializeTests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -c user.name="Lenin AGC" -c user.email="934933+IGUNUBLUE@users.noreply.github.com" \
  commit -am "feat: materialize superpowers skills from upstream tarball"
```

---

### Task 4: `.gitignore` managed block

**Files:**
- Modify: `install.py`
- Test: `tests/test_hooks.py` (new `GitignoreTests`)

**Interfaces:**
- Produces:
  - `GITIGNORE_BEGIN = "# >>> codex-session-hooks >>>"`, `GITIGNORE_END = "# <<< codex-session-hooks <<<"`
  - `gitignore_entries(repo_root) -> list[str]`
  - `ensure_gitignore(repo_root) -> bool`, `strip_gitignore(repo_root) -> bool`
- Consumes: `load_manifest` (Task 3).

- [ ] **Step 1: Failing tests**

```python
class GitignoreTests(unittest.TestCase):
    def test_block_idempotent_and_preserves_user_content(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
            self.assertTrue(installer.ensure_gitignore(repo))
            first = (repo / ".gitignore").read_text()
            self.assertFalse(installer.ensure_gitignore(repo))  # second run = no change
            self.assertEqual(first, (repo / ".gitignore").read_text())
            self.assertTrue(first.startswith("node_modules/"))
            self.assertIn("/.codex/hooks.json", first)
            self.assertIn("/.agents/skills/openspec-*/", first)

    def test_strip_removes_only_our_block(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            installer.ensure_gitignore(repo)
            (repo / ".gitignore").write_text(
                (repo / ".gitignore").read_text() + "dist/\n", encoding="utf-8")
            self.assertTrue(installer.strip_gitignore(repo))
            text = (repo / ".gitignore").read_text()
            self.assertIn("dist/", text)
            self.assertNotIn("codex-session-hooks", text)
            self.assertFalse(installer.strip_gitignore(repo))
```

- [ ] **Step 2: Run to verify failure** — FAIL, names missing.

- [ ] **Step 3: Implement**

```python
GITIGNORE_BEGIN = "# >>> codex-session-hooks >>>"
GITIGNORE_END = "# <<< codex-session-hooks <<<"


def gitignore_entries(repo_root: Path) -> list[str]:
    entries = [
        "/.codex/hooks.json",
        "/.codex/hooks/",
        "/.agents/skills/openspec-*/",
        f"/.agents/skills/{MANIFEST_NAME}",
    ]
    manifest = load_manifest(repo_root)
    entries += [
        f"/.agents/skills/{name}/"
        for name in manifest.get("superpowers", {}).get("skills", [])
    ]
    return entries


def _replace_gitignore_block(text: str, entries: list[str] | None) -> str:
    lines = text.splitlines(keepends=True)
    out, inside, found = [], False, False
    for line in lines:
        if line.strip() == GITIGNORE_BEGIN:
            inside, found = True, True
            if entries:
                out.append(GITIGNORE_BEGIN + "\n")
                out.extend(entry + "\n" for entry in entries)
            continue
        if inside and line.strip() == GITIGNORE_END:
            inside = False
            if entries:
                out.append(GITIGNORE_END + "\n")
            continue
        if not inside:
            out.append(line)
    if not found and entries:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        if out and out[-1].strip():
            out.append("\n")
        out.append(GITIGNORE_BEGIN + "\n")
        out.extend(entry + "\n" for entry in entries)
        out.append(GITIGNORE_END + "\n")
    return "".join(out)


def ensure_gitignore(repo_root: Path) -> bool:
    path = repo_root / ".gitignore"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    new = _replace_gitignore_block(old, gitignore_entries(repo_root))
    if new == old:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def strip_gitignore(repo_root: Path) -> bool:
    path = repo_root / ".gitignore"
    if not path.exists():
        return False
    old = path.read_text(encoding="utf-8")
    new = _replace_gitignore_block(old, None)
    if new == old:
        return False
    path.write_text(new, encoding="utf-8")
    return True
```

- [ ] **Step 4: Run tests** — PASS.

- [ ] **Step 5: Commit** — `feat: manage .gitignore block for repo-local install`

---

### Task 5: Global cleanup migration

**Files:**
- Modify: `install.py`
- Test: `tests/test_hooks.py` (new `GlobalCleanupTests`)

**Interfaces:**
- Produces:
  - `find_global_managed(codex_home: Path) -> set[str]`
  - `cleanup_global_hooks(codex_home: Path, tty, yes: bool) -> bool`
- Consumes: `load_hooks`, `managed_hook_id`, `merge_hooks` (Task 2), `write_hooks_atomic`.

- [ ] **Step 1: Failing tests**

```python
class GlobalCleanupTests(unittest.TestCase):
    def _global_hooks(self, codex_home):
        path = codex_home / "hooks.json"
        data = {
            "hooks": {"SessionStart": [
                {"hooks": [{"type": "command",
                            "command": "bash /x/herdr.sh session"}]},
                {"matcher": "^x$", "hooks": [
                    {"type": "command",
                     "command": f"python3 /y/openspec-context.py {installer.MARKER}",
                     "statusMessage": "Loading OpenSpec context"}]},
            ]}
        }
        path.write_text(json.dumps(data))
        return path

    def test_detects_and_removes_only_managed(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            path = self._global_hooks(codex_home)
            self.assertEqual(installer.find_global_managed(codex_home), {"openspec"})
            self.assertTrue(installer.cleanup_global_hooks(codex_home, None, True))
            data = json.loads(path.read_text())
            remaining = data["hooks"]["SessionStart"]
            self.assertEqual(len(remaining), 1)
            self.assertIn("herdr", remaining[0]["hooks"][0]["command"])
            self.assertTrue(list(codex_home.glob("hooks.json.bak.*")))  # backup kept

    def test_noop_when_absent_or_foreign_only(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            self.assertEqual(installer.find_global_managed(codex_home), set())
            self.assertFalse(installer.cleanup_global_hooks(codex_home, None, True))
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement**

```python
def find_global_managed(codex_home: Path) -> set[str]:
    path = codex_home / "hooks.json"
    if not path.exists():
        return set()
    try:
        data = load_hooks(path)
    except InstallError:
        return set()
    found: set[str] = set()
    for group in data.get("hooks", {}).get("SessionStart", []):
        if not isinstance(group, dict):
            continue
        for handler in group.get("hooks", []):
            hook_id = managed_hook_id(handler)
            if hook_id:
                found.add(hook_id)
    return found


def cleanup_global_hooks(codex_home: Path, tty, yes: bool) -> bool:
    found = find_global_managed(codex_home)
    if not found:
        return False
    path = codex_home / "hooks.json"
    if not yes:
        if tty is None:
            print(f"note: managed hooks remain in {path} (rerun with -y to remove)")
            return False
        if not confirm(tty, f"Remove managed hooks from {path}?", True):
            return False
    data = load_hooks(path)
    updated = merge_hooks(data, Path("/"), set(), found)
    if updated != data:
        backup = write_hooks_atomic(path, updated)
        print(f"Removed managed hooks from {path}"
              + (f" (backup: {backup})" if backup else ""))
    return True
```

- [ ] **Step 4: Run tests** — PASS.

- [ ] **Step 5: Commit** — `feat: migrate managed hooks out of global hooks.json`

---

### Task 6: Rewire `main()`, flags, `install.sh`; delete plugin-path code

**Files:**
- Modify: `install.py` (`main`, argparse, delete `update_superpowers`/`_official_superpowers_entry`/`_plugin_listing`)
- Modify: `install.sh`
- Test: `tests/test_hooks.py` (delete `test_discovers_superpowers_only_from_official_marketplace`)

**Interfaces:**
- Consumes: everything above.
- Produces: CLI flags `--repo DIR`, `--superpowers-ref REF`; `main()` repo flow.

- [ ] **Step 1: Delete dead code + test**

Delete `update_superpowers`, `_official_superpowers_entry`, `_plugin_listing`, and `test_discovers_superpowers_only_from_official_marketplace`. `enable_codex_hooks` stays (writes `[features] hooks=true` via `codex features enable hooks` — global capability flag, not a repo artifact).

- [ ] **Step 2: Rewire `main()`**

Argparse additions:

```python
parser.add_argument("--repo", type=Path, default=None,
                    help="target repository (default: git root of cwd)")
parser.add_argument("--superpowers-ref", default=None, metavar="REF",
                    help="obra/superpowers ref to materialize (default: latest release)")
```

`main()` body — replace the hooks.json section and add repo resolution right after tty/intro:

```python
repo_root, git_rooted = resolve_repo_root(args.repo, tty, args.yes)
if tty is not None:
    note(tty, "Target", [f"Installing hooks and skills into {repo_root}",
                         "(nothing is written to ~/.codex/hooks.json)"])
    if not confirm(tty, f"Install into {repo_root}?", True):
        raise InstallError("aborted by user")
else:
    print(f"target repo: {repo_root}")
```

Framework section:

```python
if not skip_updates:
    if "superpowers" in hooks:
        with Spinner("Materializing Superpowers skills", tty):
            materialize_superpowers(repo_root, args.superpowers_ref)
    if "openspec" in hooks:
        with Spinner("Ensuring the latest OpenSpec CLI", tty):
            update_openspec(args.openspec_package_manager)
```

Hook write section:

```python
with Spinner("Enabling Codex hooks and merging hook definitions", tty):
    enable_codex_hooks(codex_home)
    install_hook_scripts(repo_root, hooks)
    remove_hook_scripts(repo_root, args.remove)
    path = repo_root / ".codex" / "hooks.json"
    current = load_hooks(path)
    updated = merge_hooks(current, repo_root, hooks, args.remove, git_rooted)
```

Tracking policy prompt (after merge, before openspec-init):

```python
if tty is not None and not _flag_passed("--remove"):
    keep_local = confirm(
        tty, "Keep generated files local (add .gitignore entries)?", True)
    if keep_local:
        ensure_gitignore(repo_root)
    else:
        strip_gitignore(repo_root)
elif not tty:
    ensure_gitignore(repo_root)   # non-interactive default: local
```

`--openspec-init` target: default becomes `repo_root` instead of cwd (the `init_target is None` prompt branch: `if not (repo_root / "openspec" / "config.yaml").is_file(): confirm(...)` → `init_target = repo_root`).

Global cleanup near the end (before outro):

```python
cleanup_global_hooks(codex_home, tty, args.yes)
```

Outro message:

```python
message = (
    f"Done — open a Codex session in {repo_root}, trust the project, "
    "run /hooks, and approve each hook definition."
)
```

- [ ] **Step 3: `install.sh` transient mode**

Replace the persistent-dir block with:

```bash
if [ -n "$script_dir" ] && have_repo_files "$script_dir"; then
    src_dir="$script_dir"
else
    command -v tar >/dev/null 2>&1 || { echo "error: tar is required" >&2; exit 1; }
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT
    echo "Downloading ${REPO} (${REF})..."
    download "$SOURCE_URL" "$tmp_dir/repo.tar.gz"
    mkdir "$tmp_dir/extract"
    tar -xzf "$tmp_dir/repo.tar.gz" --strip-components=1 -C "$tmp_dir/extract"
    if ! have_repo_files "$tmp_dir/extract"; then
        echo "error: downloaded archive is missing installer files" >&2
        exit 1
    fi
    src_dir="$tmp_dir/extract"
fi
```

Delete `INSTALL_DIR`, its safety case, `mkdir/mv` block, and `CODEX_HOOKS_HOME` from the header comment. Keep `CODEX_HOOKS_REF`/`CODEX_HOOKS_SOURCE_URL`. The `exec python3 "$src_dir/install.py" "$@" </dev/tty` tail is unchanged — cwd passes through, so repo resolution sees the caller's repo.

- [ ] **Step 4: Full suite**

Run the Global Constraints command block. Expected: all green.

- [ ] **Step 5: Commit** — `feat!: install hooks and skills at repo scope` (breaking → v2.0.0).

---

### Task 7: Docs, version bump, real-machine verification

**Files:**
- Modify: `AGENTS.md`, `README.md`, `install.py` (`VERSION = "2.0.0"`), `docs/memory/current-state.md`, `docs/memory/decisions.md`

- [ ] **Step 1: Update docs**

- `AGENTS.md`: overview → repo-scoped install; layout → `.codex/hooks/` in target repo, manifest, materialization; invariants → replace "Absolute paths in hook commands" with "git-root-relative commands (`$(git rev-parse --show-toplevel)`), absolute fallback for non-git `--repo`"; setup commands add `--repo`.
- `README.md`: same story — usage `./install.sh --repo /path` or from inside a repo; trust flow note (`/hooks` approval, first SessionStart skipped); worktree caveat.
- `install.py`: `VERSION = "2.0.0"`.
- `docs/memory/current-state.md`: rewrite release/architecture sections.
- `docs/memory/decisions.md`: append entry — repo-scoped install, official `.agents/skills` discovery + tarball materialization chosen over submodule/vendor/global-plugin; rationale + consequences.

- [ ] **Step 2: Full suite** — all green.

- [ ] **Step 3: Real-machine verification**

```bash
rm -rf /tmp/repo-hooks-test && mkdir /tmp/repo-hooks-test && cd /tmp/repo-hooks-test && git init -q .
python3 /home/l/Projects/codex-session-hooks/install.py -y --repo /tmp/repo-hooks-test
```

Verify:
- `/tmp/repo-hooks-test/.codex/hooks.json` contains both hooks with `"$(git rev-parse --show-toplevel)/.codex/hooks/…"` commands.
- `.codex/hooks/{superpowers-bootstrap,openspec-context}.py` exist.
- `.agents/skills/using-superpowers/SKILL.md` + manifest exist.
- `.gitignore` managed block present.
- `bash -c 'cd /tmp/repo-hooks-test && python3 "$(git rev-parse --show-toplevel)/.codex/hooks/superpowers-bootstrap.py" --managed-by=codex-session-hooks'` prints `<superpowers-bootstrap>…`.
- openspec-context script prints nothing in non-openspec repo; run `--openspec-init /tmp/repo-hooks-test` then re-run script → prints `<openspec-context>`.
- Global cleanup: confirm managed entries gone from `~/.codex/hooks.json`, `herdr` entry intact.

- [ ] **Step 4: Commit** — `docs: repo-scoped install (v2.0.0)`.
