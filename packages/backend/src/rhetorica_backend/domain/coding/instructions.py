"""Versioned task instructions projected to each participant without duplicating the plan."""

import json

from rhetorica_backend.contracts.coding import CodingPlanDraft, CodingTeamMemberCapability

FORMAT = "coding-plan-v1"


def encode_instructions(task: str, plan: CodingPlanDraft | None = None) -> bytes:
    if plan is None:
        return task.encode()
    return json.dumps({"format": FORMAT, "plan": plan.model_dump()}, ensure_ascii=False).encode()


def project_instructions(payload: str, capability: CodingTeamMemberCapability | None = None) -> str:
    # Older runs and direct scenario requests contain plain task text.
    try:
        record = json.loads(payload)
    except ValueError:
        return payload
    if not isinstance(record, dict) or record.get("format") != FORMAT:
        return payload
    plan = CodingPlanDraft.model_validate(record["plan"])
    parts = ["Task:\n" + plan.task, "Acceptance criteria:\n" + "\n".join(plan.acceptance_criteria)]
    if capability is None:
        parts += [
            "Candidate assignment:\n" + plan.developer_brief,
            "Review assignment:\n" + plan.qa_brief,
        ]
    else:
        parts += [
            "Assignment:\n"
            + (
                plan.developer_brief
                if capability == CodingTeamMemberCapability.CANDIDATE
                else plan.qa_brief
            )
        ]
    return "\n\n".join(parts)
