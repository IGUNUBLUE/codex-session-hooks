#!/usr/bin/env python3
"""Inject the active Codex Superpowers bootstrap as SessionStart context."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

MAX_SKILL_CHARS = 8_000


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
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def render_bootstrap(skill_path: Path) -> str:
    content = skill_path.read_text(encoding="utf-8", errors="replace")
    if len(content) > MAX_SKILL_CHARS:
        raise ValueError(
            f"refusing to inject {len(content)} characters from {skill_path}; "
            f"limit is {MAX_SKILL_CHARS}"
        )

    reference = skill_path.parent / "references" / "codex-tools.md"
    source = json.dumps(str(skill_path))
    lines = [
        "<superpowers-bootstrap>",
        f"Automatically loaded from {source} as Codex developer context.",
        "The following is the full Superpowers using-superpowers skill:",
        "",
        content.rstrip(),
    ]
    if reference.is_file():
        lines.extend(
            [
                "",
                "Codex-specific platform guidance is available at "
                f"{json.dumps(str(reference))}. Read it when the workflow requires it.",
            ]
        )
    lines.extend(["</superpowers-bootstrap>", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--managed-by")
    parser.parse_args()

    skill_path = find_superpowers_skill()
    if not skill_path:
        return 0
    try:
        sys.stdout.write(render_bootstrap(skill_path))
    except (OSError, ValueError) as error:
        print(f"codex-session-hooks: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
