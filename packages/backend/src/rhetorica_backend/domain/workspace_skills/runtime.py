from __future__ import annotations

from rhetorica_backend.contracts.workspace_skills import (
    WorkspaceSkillAsset,
    WorkspaceSkillItem,
)

_IMPACT_PRIORITY = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
}
_MAX_PROMPT_RULES_PER_SKILL = 16
_MAX_PROMPT_CHARS = 5000


def iter_skill_assets(skill: WorkspaceSkillItem) -> list[WorkspaceSkillAsset]:
    return [
        asset
        for asset in skill.content_bundle.assets
        if asset.content_markdown.strip()
    ]


def iter_rule_assets(skill: WorkspaceSkillItem) -> list[WorkspaceSkillAsset]:
    assets = [
        asset
        for asset in iter_skill_assets(skill)
        if asset.kind == "rule" and asset.rule_id
    ]
    return sorted(
        assets,
        key=lambda asset: (
            _IMPACT_PRIORITY.get((asset.impact or "").upper(), 99),
            asset.rule_id or "",
        ),
    )


def find_rule_asset(skill: WorkspaceSkillItem, rule_id: str) -> WorkspaceSkillAsset | None:
    normalized_rule_id = rule_id.strip().lower()
    if not normalized_rule_id:
        return None
    for asset in iter_rule_assets(skill):
        if (asset.rule_id or "").strip().lower() == normalized_rule_id:
            return asset
    return None


def build_workspace_skill_prompt_guidance(skills: list[WorkspaceSkillItem]) -> str | None:
    if not skills:
        return None

    lines = [
        "Workspace skill guidance:",
        "- Treat the following workspace rules as local engine policy when you draft or repair SQL.",
        "- Prefer these rules over generic database intuition when they apply.",
    ]
    used_chars = sum(len(line) for line in lines)

    for skill in skills:
        header = f"- Skill `{skill.name}` for engine `{skill.engine_scope}`."
        if used_chars + len(header) > _MAX_PROMPT_CHARS:
            break
        lines.append(header)
        used_chars += len(header)

        rule_assets = iter_rule_assets(skill)
        if rule_assets:
            for asset in rule_assets[:_MAX_PROMPT_RULES_PER_SKILL]:
                rule_label = asset.rule_id or asset.path
                title = asset.title or rule_label
                summary = asset.summary or ""
                line = f"- `{rule_label}`: {title}. {summary}".strip()
                if used_chars + len(line) > _MAX_PROMPT_CHARS:
                    break
                lines.append(line)
                used_chars += len(line)
            continue

        fallback_summary = skill.summary or "Use this workspace skill when SQL optimization advice is relevant."
        line = f"- `{skill.name}`: {fallback_summary}"
        if used_chars + len(line) > _MAX_PROMPT_CHARS:
            break
        lines.append(line)
        used_chars += len(line)

    if len(lines) <= 3:
        return None
    return "\n".join(lines)
