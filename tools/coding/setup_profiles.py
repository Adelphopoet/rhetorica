"""Prepare explicit local coding profiles using existing connectors (never write grants)."""

import argparse
import os

from rhetorica_backend.contracts.agents import AgentSettings
from rhetorica_backend.contracts.coding import GovernedAgentSettings
from rhetorica_backend.domain.coding.bootstrap import build_coding_service
from rhetorica_backend.domain.coding.settings import CodingSettings
from rhetorica_backend.domain.coding.templates import coding_profile_templates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--login", default=os.getenv("AUTH_LOCAL_ADMIN_LOGIN", "admin"))
    parser.add_argument("--llm", required=True)
    parser.add_argument("--gitlab", required=True)
    parser.add_argument("--database", action="append", default=[])
    parser.add_argument("--enable-delegation", action="store_true")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--max-output-tokens", type=int, default=32000)
    parser.add_argument("--max-iterations", type=int, default=200)
    args = parser.parse_args()
    service = build_coding_service(CodingSettings())
    actor = service.users.get_by_login(workspace_id=args.workspace, login=args.login)
    if actor is None or not actor.is_admin:
        raise PermissionError("local_coding_setup_admin_required")
    profiles = [
        service.agents.upsert(
            request.model_copy(
                update={
                    "llm_connector_name": args.llm,
                    "allowed_gitlab_connector_names": [args.gitlab],
                    "allowed_database_connector_names": args.database,
                    "settings": AgentSettings(
                        temperature=args.temperature, max_tokens=args.max_output_tokens
                    ),
                }
            ),
            actor_user_id=actor.id,
        )
        for request in coding_profile_templates(workspace_id=args.workspace)
    ]
    for index, profile in enumerate(profiles):
        current = service.policies.get_agent_policy(
            workspace_id=args.workspace, agent_id=profile.id
        )
        policy = GovernedAgentSettings(
            revision=current.revision,
            max_iterations=args.max_iterations,
            max_runtime_seconds=1200,
            allowed_subagent_ids=[p.id for p in profiles[1:]]
            if index == 0 and args.enable_delegation
            else [],
        )
        service.policies.replace_agent_policy(
            workspace_id=args.workspace,
            agent_id=profile.id,
            actor_user_id=actor.id,
            settings=policy,
        )
        print(profile.title, profile.id, "publication=closed")


if __name__ == "__main__":
    main()
