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
_MAX_CHAT_SKILL_CATALOG_CHARS = 4000


def iter_skill_assets(skill: WorkspaceSkillItem) -> list[WorkspaceSkillAsset]:
    return [asset for asset in skill.content_bundle.assets if asset.content_markdown.strip()]


def iter_rule_assets(skill: WorkspaceSkillItem) -> list[WorkspaceSkillAsset]:
    assets = [asset for asset in iter_skill_assets(skill) if asset.kind == "rule" and asset.rule_id]
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


def skill_use_when(skill: WorkspaceSkillItem, fallback: str) -> str:
    if skill.summary:
        return skill.summary
    for asset in iter_skill_assets(skill):
        if asset.kind == "main" and asset.summary:
            return asset.summary
    return fallback


def build_workspace_chat_skill_catalog(skills: list[WorkspaceSkillItem]) -> str | None:
    """Build a compact prompt catalog for on-demand chat instruction skills."""
    chat_skills = [
        skill
        for skill in skills
        if skill.capability_family == "chat_instruction"
        and skill.status == "active"
        and skill.validation_status == "valid"
    ]
    if not chat_skills:
        return None

    lines = ["Workspace chat skills catalog:"]
    used_chars = sum(len(line) for line in lines)
    for skill in chat_skills:
        title = skill.title or skill.name
        use_when = skill_use_when(skill, "No usage guidance provided.")
        line = (
            f"- id={skill.id}; name={skill.name}; title={title}; "
            f"priority={skill.priority}; use_when={use_when}"
        )
        if used_chars + len(line) > _MAX_CHAT_SKILL_CATALOG_CHARS:
            break
        lines.append(line)
        used_chars += len(line)

    if len(lines) == 1:
        return None
    return "\n".join(lines)
