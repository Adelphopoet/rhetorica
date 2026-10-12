from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import workspace_skills as workspace_skill_routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.domain.workspace_skills.service import WorkspaceSkillService
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_skill_repository import WorkspaceSkillRepository


class _HttpClientStub:
    def __init__(self, responses: dict[str, httpx.Response]) -> None:
        self._responses = responses

    def get(self, url: str) -> httpx.Response:
        return self._responses.get(url, httpx.Response(404))


def _client(tmp_path: Path, http_client: _HttpClientStub, monkeypatch) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/api_workspace_skills.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    workspace_skill_routes._service.cache_clear()

    runtime = DatabaseRuntime(settings.database_url)
    runtime.create_schema()
    service = WorkspaceSkillService(
        WorkspaceSkillRepository(db=runtime),
        http_client=http_client,
    )
    monkeypatch.setattr(workspace_skill_routes, "_service", lambda: service)
    return TestClient(create_app())


def test_workspace_skill_crud(tmp_path: Path, monkeypatch) -> None:
    raw_source = "https://raw.githubusercontent.com/acme/playbooks/main/clickhouse/optimizer.md"
    client = _client(
        tmp_path,
        _HttpClientStub({
            raw_source: httpx.Response(
                200,
                headers={"content-type": "text/markdown; charset=utf-8"},
                content=b"# Join optimizer\nPush filters to scans.\n",
            ),
        }),
        monkeypatch,
    )

    create_response = client.post(
        "/v1/admin/skills",
        json={
            "name": "join-guide",
            "workspace_id": "default",
            "capability_family": "chat_instruction",
            "engine_scope": "clickhouse",
            "priority": 250,
            "source_kind": "github_markdown_url",
            "source_url": "https://github.com/acme/playbooks/blob/main/clickhouse/optimizer.md",
            "status": "active",
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["source_url"] == raw_source
    assert created["title"] == "Join optimizer"
    assert created["validation_status"] == "valid"
    skill_id = created["id"]

    list_response = client.get("/v1/admin/skills", params={"workspace_id": "default"})
    assert list_response.status_code == 200
    rows = list_response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == skill_id

    update_response = client.put(
        f"/v1/admin/skills/{skill_id}",
        params={"workspace_id": "default"},
        json={
        "name": "join-guide-v2",
            "workspace_id": "default",
            "capability_family": "chat_instruction",
            "engine_scope": "any",
            "priority": 300,
            "source_kind": "github_markdown_url",
            "source_url": raw_source,
            "status": "inactive",
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "join-guide-v2"
    assert updated["engine_scope"] == "any"
    assert updated["status"] == "inactive"

    delete_response = client.delete(
        f"/v1/admin/skills/{skill_id}",
        params={"workspace_id": "default"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_workspace_skill_accepts_trino_engine_scope(tmp_path: Path, monkeypatch) -> None:
    raw_source = (
        "https://raw.githubusercontent.com/Adelphopoet/trino-best-practices-skill/main/"
        "skills/trino-best-practices/SKILL.md"
    )
    client = _client(
        tmp_path,
        _HttpClientStub({
            raw_source: httpx.Response(
                200,
                headers={"content-type": "text/markdown; charset=utf-8"},
                content=b"# Trino Best Practices\nReview Trino plans first.\n",
            ),
        }),
        monkeypatch,
    )

    response = client.post(
        "/v1/admin/skills",
        json={
            "name": "trino-best-practices",
            "workspace_id": "default",
            "capability_family": "chat_instruction",
            "engine_scope": "trino",
            "priority": 180,
            "source_kind": "github_markdown_url",
            "source_url": "https://github.com/Adelphopoet/trino-best-practices-skill",
            "status": "active",
        },
    )

    assert response.status_code == 200
    created = response.json()
    assert created["engine_scope"] == "any"
    assert created["source_url"] == raw_source


def test_workspace_skill_crud_manual_chat_instruction(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, _HttpClientStub({}), monkeypatch)

    create_response = client.post(
        "/v1/admin/skills",
        json={
            "name": "support-tone",
            "workspace_id": "default",
            "capability_family": "chat_instruction",
            "engine_scope": "clickhouse",
            "priority": 50,
            "source_kind": "manual_markdown",
            "content_markdown": "# Support tone\nAnswer briefly.\n",
            "status": "active",
            "visible_to_default_assistant": False,
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["source_kind"] == "manual_markdown"
    assert created["source_url"] is None
    assert created["engine_scope"] == "any"
    assert created["visible_to_default_assistant"] is False
    assert created["title"] == "Support tone"
    assert created["content_markdown"] == "# Support tone\nAnswer briefly.\n"

    skill_id = created["id"]
    update_response = client.put(
        f"/v1/admin/skills/{skill_id}",
        params={"workspace_id": "default"},
        json={
            "name": "support-tone-v2",
            "workspace_id": "default",
            "capability_family": "chat_instruction",
            "engine_scope": "trino",
            "priority": 60,
            "source_kind": "manual_markdown",
            "content_markdown": "# Support tone\nAsk one concise question.\n",
            "status": "active",
            "visible_to_default_assistant": True,
        },
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "support-tone-v2"
    assert updated["engine_scope"] == "any"
    assert updated["visible_to_default_assistant"] is True
    assert updated["summary"] == "Ask one concise question."


def test_workspace_skill_rejects_source_kind_payload_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, _HttpClientStub({}), monkeypatch)

    response = client.post(
        "/v1/admin/skills",
        json={
            "name": "bad-manual",
            "workspace_id": "default",
            "capability_family": "chat_instruction",
            "engine_scope": "any",
            "priority": 50,
            "source_kind": "manual_markdown",
            "source_url": "https://github.com/acme/playbooks/blob/main/skill.md",
            "status": "active",
        },
    )

    assert response.status_code == 422


def test_workspace_skill_accepts_greenplum_engine_scope(tmp_path: Path, monkeypatch) -> None:
    raw_source = (
        "https://raw.githubusercontent.com/Adelphopoet/greenplum-best-practices-skill/main/"
        "skills/greenplum-best-practices/SKILL.md"
    )
    client = _client(
        tmp_path,
        _HttpClientStub({
            raw_source: httpx.Response(
                200,
                headers={"content-type": "text/markdown; charset=utf-8"},
                content=b"# Greenplum Best Practices\nReview Greenplum evidence first.\n",
            ),
        }),
        monkeypatch,
    )

    response = client.post(
        "/v1/admin/skills",
        json={
            "name": "greenplum-best-practices",
            "workspace_id": "default",
            "capability_family": "chat_instruction",
            "engine_scope": "greenplum",
            "priority": 170,
            "source_kind": "github_markdown_url",
            "source_url": "https://github.com/Adelphopoet/greenplum-best-practices-skill",
            "status": "active",
        },
    )

    assert response.status_code == 200
    created = response.json()
    assert created["engine_scope"] == "any"
    assert created["source_url"] == raw_source


def test_workspace_skill_rejects_invalid_source_url(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, _HttpClientStub({}), monkeypatch)

    response = client.post(
        "/v1/admin/skills",
        json={
            "name": "bad-skill",
            "workspace_id": "default",
            "capability_family": "chat_instruction",
            "engine_scope": "clickhouse",
            "priority": 100,
            "source_kind": "github_markdown_url",
            "source_url": "https://example.com/skill.md",
            "status": "active",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "workspace_skill_invalid_source_url"


def test_workspace_skill_rejects_binary_payload(tmp_path: Path, monkeypatch) -> None:
    raw_source = "https://raw.githubusercontent.com/acme/playbooks/main/clickhouse/optimizer.md"
    client = _client(
        tmp_path,
        _HttpClientStub({
            raw_source: httpx.Response(
                200,
                headers={"content-type": "application/octet-stream"},
                content=b"# nope\x00",
            ),
        }),
        monkeypatch,
    )

    response = client.post(
        "/v1/admin/skills",
        json={
            "name": "bad-binary",
            "workspace_id": "default",
            "capability_family": "chat_instruction",
            "engine_scope": "clickhouse",
            "priority": 100,
            "source_kind": "github_markdown_url",
            "source_url": raw_source,
            "status": "active",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "workspace_skill_binary_content"


def test_workspace_skill_rejects_removed_optimizer_family(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, _HttpClientStub({}), monkeypatch)

    response = client.post(
        "/v1/admin/skills",
        json={
            "name": "legacy-optimizer",
            "workspace_id": "default",
            "capability_family": "sql_optimization_advisor",
            "source_kind": "manual_markdown",
            "content_markdown": "# Legacy optimizer\n",
        },
    )

    assert response.status_code == 422
    assert "workspace_skill_legacy_capability_family" in str(response.json())
