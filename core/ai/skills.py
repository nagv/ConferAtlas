"""Load reusable writing skills from ``skills/<name>/``.

A skill folder holds ``SKILL.md`` (YAML frontmatter plus instructions, an
"Output structure" list and a "Checklist"), ``prompt.md`` (a template with
``$placeholders`` and ``<!-- system -->`` / ``<!-- user -->`` parts), and
optionally an example (``example_facts.json`` + ``example_report.md``).
The code reads the section list and checklist from SKILL.md itself, so
editing the skill changes both the prompt and the checks.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from core.config import PROJECT_ROOT

SKILLS_DIR = PROJECT_ROOT / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str        # SKILL.md body without the frontmatter
    sections: tuple[str, ...]
    checklist: tuple[str, ...]
    system_prompt: str
    user_template: str
    example_facts: dict | None
    example_report: str | None
    path: Path


def _section(body: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", body, flags=re.M | re.S)
    if not match:
        raise ValueError(f"SKILL.md has no '## {heading}' section")
    return match.group(1)


def load_skill(name: str, skills_dir: Path = SKILLS_DIR) -> Skill:
    folder = skills_dir / name
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    front = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, flags=re.S)
    if not front:
        raise ValueError(f"{folder / 'SKILL.md'} needs YAML frontmatter with name and description")
    meta = dict(re.findall(r"^(\w+):\s*(.+)$", front.group(1), flags=re.M))
    body = front.group(2).strip()

    # Sections: the top-level "- Name" bullets that come before the section guidance.
    structure = _section(body, "Output structure")
    sections = tuple(re.findall(r"^- (?!\*\*)(.+?)\s*$", structure.split("Section guidance")[0], flags=re.M))
    checklist = tuple(re.findall(r"^- \[ \] (.+?)\s*$", _section(body, "Checklist"), flags=re.M))
    if not sections or not checklist:
        raise ValueError("SKILL.md must list the output sections and checklist items")

    prompt = (folder / "prompt.md").read_text(encoding="utf-8")
    parts = re.split(r"<!--\s*(system|user)\s*-->", prompt)
    prompts = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    facts_path, report_path = folder / "example_facts.json", folder / "example_report.md"
    return Skill(
        name=meta.get("name", name), description=meta.get("description", ""), instructions=body,
        sections=sections, checklist=checklist,
        system_prompt=prompts.get("system", ""), user_template=prompts.get("user", prompt),
        example_facts=json.loads(facts_path.read_text(encoding="utf-8")) if facts_path.exists() else None,
        example_report=report_path.read_text(encoding="utf-8") if report_path.exists() else None,
        path=folder,
    )
