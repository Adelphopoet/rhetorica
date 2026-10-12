from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from rhetorica_backend.contracts.workspace_skills import (
    UpsertGithubWorkspaceSkillRequest,
    UpsertGitLabWorkspaceSkillRequest,
    UpsertManualWorkspaceSkillRequest,
    WorkspaceSkillItem,
)
from rhetorica_backend.domain.workspace_skills.service import (
    WorkspaceSkillService,
    WorkspaceSkillValidationError,
)


class _FakeRepository:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> WorkspaceSkillItem:
        self.create_calls.append(kwargs)
        now = datetime.now(tz=UTC)
        return WorkspaceSkillItem(
            id="skill-1",
            created_at=now,
            updated_at=now,
            **kwargs,
        )


class _StaticHttpClient:
    def __init__(self, responses: httpx.Response | dict[str, httpx.Response]) -> None:
        self._responses = responses

    def get(self, url: str) -> httpx.Response:
        if isinstance(self._responses, dict):
            return self._responses.get(url, httpx.Response(404))
        return self._responses


class _FailingHttpClient:
    def get(self, url: str) -> httpx.Response:
        _ = url
        raise AssertionError("manual workspace skills must not use HTTP")


class _FakeGitLabConnectorRepository:
    def get_connection(self, **_kwargs: object) -> object:
        return object()


class _FakeGitLabService:
    def __init__(self) -> None:
        self.read_refs: list[str] = []
        self.resolve_calls = 0
        self.list_tree_calls = 0

    def resolve_commit(self, _connection: object, **_kwargs: object) -> str:
        self.resolve_calls += 1
        return "a" * 40

    def list_tree(self, _connection: object, **_kwargs: object) -> dict[str, object]:
        self.list_tree_calls += 1
        return {
            "truncated": False,
            "items": [
                {"type": "blob", "mode": "100644", "path": "skills/SKILL.md"},
                {"type": "blob", "mode": "100755", "path": "skills/setup.sh"},
                {"type": "blob", "mode": "120000", "path": "skills/linked.md"},
                {"type": "blob", "mode": "100644", "path": "skills/rules.md"},
            ],
        }

    def read_file(self, _connection: object, **kwargs: object) -> dict[str, object]:
        ref = kwargs["ref"]
        assert isinstance(ref, str)
        self.read_refs.append(ref)
        path = kwargs["file_path"]
        assert isinstance(path, str)
        return {
            "content": "# Main\nPinned guidance\n" if path.endswith("SKILL.md") else "# Rules\n",
            "truncated": False,
            "url": f"https://gitlab.example/group/project/-/blob/{ref}/{path}",
        }


def test_create_normalizes_blob_url_and_snapshots_markdown() -> None:
    repository = _FakeRepository()
    service = WorkspaceSkillService(
        repository,
        http_client=_StaticHttpClient(
            httpx.Response(
                200,
                headers={"content-type": "text/markdown; charset=utf-8"},
                content=b"# ClickHouse joins\nPush filters early.\n",
            )
        ),
    )

    skill = service.create(
        UpsertGithubWorkspaceSkillRequest(
            name="optimizer",
            workspace_id="default",
            source_url="https://github.com/acme/playbooks/blob/main/clickhouse/optimizer.md",
        )
    )

    assert skill.source_url == (
        "https://raw.githubusercontent.com/acme/playbooks/main/clickhouse/optimizer.md"
    )
    assert skill.title == "ClickHouse joins"
    assert skill.summary == "Push filters early."
    assert repository.create_calls[0]["validation_status"] == "valid"
    assert repository.create_calls[0]["content_sha256"] is not None


def test_create_accepts_clickhouse_agent_skills_repo_url() -> None:
    repository = _FakeRepository()
    service = WorkspaceSkillService(
        repository,
        http_client=_StaticHttpClient(
            httpx.Response(
                200,
                headers={"content-type": "text/markdown; charset=utf-8"},
                content=(
                    b"---\n"
                    b"name: clickhouse-best-practices\n"
                    b"description: MUST USE.\n"
                    b"---\n\n"
                    b"# ClickHouse Best Practices\n\n"
                    b"Official ClickHouse skill.\n"
                ),
            )
        ),
    )

    skill = service.create(
        UpsertGithubWorkspaceSkillRequest(
            name="clickhouse-best-practices",
            workspace_id="default",
            source_url="https://github.com/ClickHouse/agent-skills",
        )
    )

    assert skill.source_url == (
        "https://raw.githubusercontent.com/ClickHouse/agent-skills/main/"
        "skills/clickhouse-best-practices/SKILL.md"
    )
    assert skill.title == "ClickHouse Best Practices"
    assert skill.summary == "Official ClickHouse skill."


def test_create_manual_chat_skill_snapshots_markdown_without_http() -> None:
    repository = _FakeRepository()
    service = WorkspaceSkillService(repository, http_client=_FailingHttpClient())

    skill = service.create(
        UpsertManualWorkspaceSkillRequest(
            name="support-tone",
            workspace_id="default",
            capability_family="chat_instruction",
            engine_scope="clickhouse",
            source_kind="manual_markdown",
            content_markdown="# Support tone\nAnswer briefly and ask one question.\n",
        )
    )

    assert skill.source_url is None
    assert skill.engine_scope == "any"
    assert skill.title == "Support tone"
    assert skill.summary == "Answer briefly and ask one question."
    assert skill.content_sha256 is not None
    assert skill.content_bundle.assets[0].source_url is None
    assert repository.create_calls[0]["engine_scope"] == "any"


def test_create_gitlab_bundle_pins_sha_and_skips_scripts_and_symlinks() -> None:
    repository = _FakeRepository()
    gitlab = _FakeGitLabService()
    service = WorkspaceSkillService(
        repository,
        http_client=_FailingHttpClient(),
        gitlab_connector_repository=_FakeGitLabConnectorRepository(),  # type: ignore[arg-type]
        gitlab_service=gitlab,  # type: ignore[arg-type]
    )

    skill = service.create(
        UpsertGitLabWorkspaceSkillRequest(
            name="pinned-skill",
            connector_name="internal",
            project_path="group/project",
            ref="main",
            path="skills",
        )
    )

    assert skill.source_url == f"gitlab://internal/group/project@{'a' * 40}/skills"
    assert [asset.path for asset in skill.content_bundle.assets] == [
        "skills/SKILL.md",
        "skills/rules.md",
    ]
    assert [asset.kind for asset in skill.content_bundle.assets] == ["main", "doc"]
    assert skill.content_markdown == "# Main\nPinned guidance\n"
    assert gitlab.read_refs == ["a" * 40, "a" * 40]


def test_create_gitlab_bundle_uses_skill_md_as_main_regardless_of_tree_order() -> None:
    class _ReadmeFirstGitLabService(_FakeGitLabService):
        def list_tree(self, _connection: object, **_kwargs: object) -> dict[str, object]:
            self.list_tree_calls += 1
            return {
                "truncated": False,
                "items": [
                    {"type": "blob", "mode": "100644", "path": "skills/README.md"},
                    {"type": "blob", "mode": "100644", "path": "skills/SKILL.md"},
                ],
            }

        def read_file(self, _connection: object, **kwargs: object) -> dict[str, object]:
            path = kwargs["file_path"]
            assert isinstance(path, str)
            return {
                "content": "# Skill\nPrimary guidance\n" if path.endswith("SKILL.md") else "# Readme\nNot instructions\n",
                "truncated": False,
            }

    gitlab = _ReadmeFirstGitLabService()
    skill = WorkspaceSkillService(
        _FakeRepository(),
        http_client=_FailingHttpClient(),
        gitlab_connector_repository=_FakeGitLabConnectorRepository(),  # type: ignore[arg-type]
        gitlab_service=gitlab,  # type: ignore[arg-type]
    ).create(
        UpsertGitLabWorkspaceSkillRequest(
            name="pinned-skill",
            connector_name="internal",
            project_path="group/project",
            ref="main",
            path="skills",
        )
    )

    assert skill.content_markdown == "# Skill\nPrimary guidance\n"
    assert [asset.kind for asset in skill.content_bundle.assets] == ["main", "doc"]


@pytest.mark.parametrize("path", ["../skills", "skills/../private", "skills\x00private"])
def test_create_gitlab_bundle_rejects_unsafe_path_before_gitlab_calls(path: str) -> None:
    gitlab = _FakeGitLabService()
    service = WorkspaceSkillService(
        _FakeRepository(),
        http_client=_FailingHttpClient(),
        gitlab_connector_repository=_FakeGitLabConnectorRepository(),  # type: ignore[arg-type]
        gitlab_service=gitlab,  # type: ignore[arg-type]
    )

    with pytest.raises(WorkspaceSkillValidationError, match="workspace_skill_gitlab_path_invalid"):
        service.create(
            UpsertGitLabWorkspaceSkillRequest(
                name="unsafe-skill",
                connector_name="internal",
                project_path="group/project",
                ref="main",
                path=path,
            )
        )

    assert gitlab.resolve_calls == 0
    assert gitlab.list_tree_calls == 0


def test_create_manual_skill_uses_frontmatter_use_when_as_compact_summary() -> None:
    service = WorkspaceSkillService(_FakeRepository(), http_client=_FailingHttpClient())

    skill = service.create(
        UpsertManualWorkspaceSkillRequest(
            name="support-tone",
            source_kind="manual_markdown",
            capability_family="chat_instruction",
            content_markdown=(
                "---\n"
                "title: Support tone\n"
                "use_when: Use when drafting support-facing replies.\n"
                "---\n\n"
                "# Support tone\n"
                "Full private instruction body.\n"
            ),
        )
    )

    assert skill.summary == "Use when drafting support-facing replies."


def test_create_manual_skill_rejects_empty_or_binary_content() -> None:
    service = WorkspaceSkillService(_FakeRepository(), http_client=_FailingHttpClient())

    with pytest.raises(WorkspaceSkillValidationError, match="workspace_skill_empty_content"):
        service.create(
            UpsertManualWorkspaceSkillRequest(
                name="empty",
                source_kind="manual_markdown",
                content_markdown="   ",
            )
        )

    with pytest.raises(WorkspaceSkillValidationError, match="workspace_skill_binary_content"):
        service.create(
            UpsertManualWorkspaceSkillRequest(
                name="binary",
                source_kind="manual_markdown",
                content_markdown="# Bad\x00",
            )
        )


def test_create_accepts_trino_skill_repo_url() -> None:
    repository = _FakeRepository()
    service = WorkspaceSkillService(
        repository,
        http_client=_StaticHttpClient(
            httpx.Response(
                200,
                headers={"content-type": "text/markdown; charset=utf-8"},
                content=(
                    b"---\n"
                    b"name: trino-best-practices\n"
                    b"description: MUST USE.\n"
                    b"---\n\n"
                    b"# Trino Best Practices\n\n"
                    b"Connector-aware Trino review guide.\n"
                ),
            )
        ),
    )

    skill = service.create(
        UpsertGithubWorkspaceSkillRequest(
            name="trino-best-practices",
            workspace_id="default",
            engine_scope="trino",
            source_url="https://github.com/Adelphopoet/trino-best-practices-skill",
        )
    )

    assert skill.source_url == (
        "https://raw.githubusercontent.com/Adelphopoet/trino-best-practices-skill/main/"
        "skills/trino-best-practices/SKILL.md"
    )
    assert skill.title == "Trino Best Practices"
    assert skill.summary == "Connector-aware Trino review guide."


def test_create_accepts_greenplum_skill_repo_url() -> None:
    repository = _FakeRepository()
    service = WorkspaceSkillService(
        repository,
        http_client=_StaticHttpClient(
            httpx.Response(
                200,
                headers={"content-type": "text/markdown; charset=utf-8"},
                content=(
                    b"---\n"
                    b"name: greenplum-best-practices\n"
                    b"description: MUST USE.\n"
                    b"---\n\n"
                    b"# Greenplum Best Practices\n\n"
                    b"Review-first guidance for Greenplum 6.x.\n"
                ),
            )
        ),
    )

    skill = service.create(
        UpsertGithubWorkspaceSkillRequest(
            name="greenplum-best-practices",
            workspace_id="default",
            engine_scope="greenplum",
            source_url="https://github.com/Adelphopoet/greenplum-best-practices-skill",
        )
    )

    assert skill.source_url == (
        "https://raw.githubusercontent.com/Adelphopoet/greenplum-best-practices-skill/main/"
        "skills/greenplum-best-practices/SKILL.md"
    )
    assert skill.title == "Greenplum Best Practices"
    assert skill.summary == "Review-first guidance for Greenplum 6.x."


def test_create_snapshots_clickhouse_skill_bundle() -> None:
    repository = _FakeRepository()
    main_url = (
        "https://raw.githubusercontent.com/ClickHouse/agent-skills/main/"
        "skills/clickhouse-best-practices/SKILL.md"
    )
    tree_url = "https://api.github.com/repos/ClickHouse/agent-skills/git/trees/main?recursive=1"
    rule_url = (
        "https://raw.githubusercontent.com/ClickHouse/agent-skills/main/"
        "skills/clickhouse-best-practices/rules/query-join-filter-before.md"
    )
    compiled_url = (
        "https://raw.githubusercontent.com/ClickHouse/agent-skills/main/"
        "skills/clickhouse-best-practices/AGENTS.md"
    )
    service = WorkspaceSkillService(
        repository,
        http_client=_StaticHttpClient(
            {
            main_url: httpx.Response(
                200,
                headers={"content-type": "text/markdown; charset=utf-8"},
                content=(
                    b"---\n"
                    b"name: clickhouse-best-practices\n"
                    b"description: MUST USE.\n"
                    b"---\n\n"
                    b"# ClickHouse Best Practices\n\n"
                    b"Official ClickHouse skill.\n"
                ),
            ),
            tree_url: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "tree": [
                        {
                            "path": "skills/clickhouse-best-practices/SKILL.md",
                            "type": "blob",
                        },
                        {
                            "path": "skills/clickhouse-best-practices/AGENTS.md",
                            "type": "blob",
                        },
                        {
                            "path": (
                                "skills/clickhouse-best-practices/"
                                "rules/query-join-filter-before.md"
                            ),
                            "type": "blob",
                        },
                        {
                            "path": "skills/clickhouse-best-practices/rules/_template.md",
                            "type": "blob",
                        },
                    ]
                },
            ),
            compiled_url: httpx.Response(
                200,
                headers={"content-type": "text/markdown; charset=utf-8"},
                content=b"# Full Guide\nCompiled ClickHouse guide.\n",
            ),
            rule_url: httpx.Response(
                200,
                headers={"content-type": "text/markdown; charset=utf-8"},
                content=(
                    b"---\n"
                    b"title: Filter Tables Before Joining\n"
                    b"impact: CRITICAL\n"
                    b"tags: [query, JOIN, filtering]\n"
                    b"---\n\n"
                    b"## Filter Tables Before Joining\n\n"
                    b"Joining full tables then filtering wastes resources.\n"
                ),
            ),
        }
        ),
    )

    skill = service.create(
        UpsertGithubWorkspaceSkillRequest(
            name="clickhouse-best-practices",
            workspace_id="default",
            source_url="https://github.com/ClickHouse/agent-skills",
        )
    )

    asset_paths = [asset["path"] for asset in repository.create_calls[0]["content_bundle"]["assets"]]
    assert skill.content_bundle.assets[0].kind == "main"
    assert "skills/clickhouse-best-practices/AGENTS.md" in asset_paths
    assert "skills/clickhouse-best-practices/rules/query-join-filter-before.md" in asset_paths
    rule_asset = next(
        asset for asset in skill.content_bundle.assets if asset.rule_id == "query-join-filter-before"
    )
    assert rule_asset.kind == "rule"
    assert rule_asset.impact == "CRITICAL"
    assert rule_asset.tags == ["query", "JOIN", "filtering"]
    assert rule_asset.summary == "Joining full tables then filtering wastes resources."


def test_create_rejects_non_github_urls() -> None:
    repository = _FakeRepository()
    service = WorkspaceSkillService(
        repository,
        http_client=_StaticHttpClient(httpx.Response(200, content=b"# Title\n")),
    )

    with pytest.raises(WorkspaceSkillValidationError, match="workspace_skill_invalid_source_url"):
        service.create(
            UpsertGithubWorkspaceSkillRequest(
                name="optimizer",
                workspace_id="default",
                source_url="https://example.com/skill.md",
            )
        )


def test_create_rejects_binary_payloads() -> None:
    repository = _FakeRepository()
    service = WorkspaceSkillService(
        repository,
        http_client=_StaticHttpClient(
            httpx.Response(
                200,
                headers={"content-type": "application/octet-stream"},
                content=b"# bad\x00payload",
            )
        ),
    )

    with pytest.raises(WorkspaceSkillValidationError, match="workspace_skill_binary_content"):
        service.create(
            UpsertGithubWorkspaceSkillRequest(
                name="optimizer",
                workspace_id="default",
                source_url="https://raw.githubusercontent.com/acme/playbooks/main/optimizer.md",
            )
        )
