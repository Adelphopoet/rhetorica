from types import SimpleNamespace

import pytest
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.agent_profile_resolver import (
    AgentProfileResolutionError,
    AgentProfileResolver,
)


def _agent(agent_id: str = "agent-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=agent_id,
        admin_prompt="Answer as a finance analyst.",
        allowed_database_connector_names=["warehouse"],
        allowed_rag_connector_names=[],
        allowed_documentation_connector_names=["handbook"],
        allowed_gitlab_connector_names=[],
        allowed_mcp_connector_names=["metadata"],
        allowed_airflow_connector_names=[],
        allowed_kubernetes_connector_names=[],
        allowed_victoriametrics_connector_names=[],
        allowed_workspace_skill_ids=["skill-1"],
        llm_connector_name="agent-llm",
        settings=SimpleNamespace(run_sql_allowed=False, temperature=0.2, max_tokens=1234),
    )


class _Repository:
    def __init__(self, agent: SimpleNamespace | None) -> None:
        self.agent = agent
        self.calls: list[dict[str, object]] = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self.agent


def test_resolver_applies_agent_runtime_policy() -> None:
    repository = _Repository(_agent())
    request = ChatAskRequest(
        question="Revenue?",
        workspace_id="workspace-1",
        agent_id="agent-1",
        run_sql=True,
    )

    resolved, agent = AgentProfileResolver(repository).resolve(
        request=request,
        current_user_id="user-1",
    )

    assert agent is repository.agent
    assert resolved.agent_prompt == "Answer as a finance analyst."
    assert resolved.agent_allowed_database_connector_names == ["warehouse"]
    assert resolved.agent_allowed_documentation_connector_names == ["handbook"]
    assert resolved.agent_allowed_mcp_connector_names == ["metadata"]
    assert resolved.agent_allowed_workspace_skill_ids == ["skill-1"]
    assert resolved.llm_connector_name == "agent-llm"
    assert resolved.run_sql is False
    assert resolved.agent_temperature == 0.2
    assert resolved.agent_max_tokens == 1234
    assert repository.calls == [
        {
            "agent_id": "agent-1",
            "workspace_id": "workspace-1",
            "current_user_id": "user-1",
            "active_only": True,
        }
    ]


def test_resolver_restores_conversation_agent_when_request_omits_it() -> None:
    repository = _Repository(_agent())
    request = ChatAskRequest(question="Follow up", workspace_id="workspace-1")

    resolved, _ = AgentProfileResolver(repository).resolve(
        request=request,
        current_user_id="user-1",
        existing_conversation=SimpleNamespace(agent_id="agent-1"),
    )

    assert resolved.agent_id == "agent-1"


@pytest.mark.parametrize(
    ("conversation_agent_id", "requested_agent_id"),
    [("agent-1", "agent-2"), (None, "agent-1")],
)
def test_resolver_rejects_agent_switch(
    conversation_agent_id: str | None,
    requested_agent_id: str,
) -> None:
    request = ChatAskRequest(
        question="Switch",
        workspace_id="workspace-1",
        agent_id=requested_agent_id,
    )

    with pytest.raises(AgentProfileResolutionError, match="conversation_agent_locked"):
        AgentProfileResolver(_Repository(_agent())).resolve(
            request=request,
            current_user_id="user-1",
            existing_conversation=SimpleNamespace(agent_id=conversation_agent_id),
        )


def test_resolver_rejects_missing_or_inactive_agent() -> None:
    request = ChatAskRequest(
        question="Revenue?",
        workspace_id="workspace-1",
        agent_id="missing-agent",
    )

    with pytest.raises(AgentProfileResolutionError, match="agent_not_found"):
        AgentProfileResolver(_Repository(None)).resolve(
            request=request,
            current_user_id="user-1",
        )
