from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit, urlunsplit

import httpx
from rhetorica_backend.contracts.workspace_skills import (
    BaseUpsertWorkspaceSkillRequest,
    UpsertGithubWorkspaceSkillRequest,
    UpsertGitLabWorkspaceSkillRequest,
    UpsertManualWorkspaceSkillRequest,
    UpsertWorkspaceSkillRequest,
    WorkspaceSkillAsset,
    WorkspaceSkillBundle,
    WorkspaceSkillItem,
)
from rhetorica_backend.domain.chat.gitlab.service import GitLabRequestError, GitLabService
from rhetorica_backend.infra.postgres.gitlab_connector_repository import GitLabConnectorRepository
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)

_GITHUB_BLOB_RE = re.compile(r"^/([^/]+)/([^/]+)/blob/([^/]+)/(.+\.md)$", re.IGNORECASE)
_RAW_GITHUB_RE = re.compile(r"^/([^/]+)/([^/]+)/([^/]+)/(.+\.md)$", re.IGNORECASE)
_GITHUB_REPO_RE = re.compile(r"^/([^/]+)/([^/]+)/?$", re.IGNORECASE)
_GITHUB_TREE_RE = re.compile(r"^/([^/]+)/([^/]+)/tree/([^/]+)(?:/(.*))?$", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_KNOWN_GITHUB_SKILL_REPOS: dict[tuple[str, str], str] = {
    ("clickhouse", "agent-skills"): "skills/clickhouse-best-practices/SKILL.md",
    ("adelphopoet", "trino-best-practices-skill"): "skills/trino-best-practices/SKILL.md",
    ("adelphopoet", "greenplum-best-practices-skill"): "skills/greenplum-best-practices/SKILL.md",
}


class WorkspaceSkillValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedWorkspaceSkillPayload:
    source_url: str | None
    title: str | None
    summary: str | None
    content_markdown: str
    content_bundle: WorkspaceSkillBundle
    content_sha256: str
    version_digest: str
    validation_status: str
    last_validated_at: datetime


class WorkspaceSkillService:
    """Validates, snapshots, and persists workspace skills."""

    _MAX_CONTENT_BYTES = 262_144
    _MAX_MANUAL_CONTENT_BYTES = 32_768
    _MAX_BUNDLE_BYTES = 1_048_576
    _MAX_BUNDLE_ASSETS = 64

    def __init__(
        self,
        repository: WorkspaceSkillRepository,
        *,
        http_client: httpx.Client | None = None,
        gitlab_connector_repository: GitLabConnectorRepository | None = None,
        gitlab_service: GitLabService | None = None,
    ) -> None:
        self._repository = repository
        self._http_client = http_client or httpx.Client(timeout=15.0, follow_redirects=True)
        self._gitlab_connector_repository = gitlab_connector_repository
        self._gitlab_service = gitlab_service or GitLabService()

    def list(self, workspace_id: str, limit: int) -> list[WorkspaceSkillItem]:
        return self._repository.list(workspace_id=workspace_id, limit=limit)

    def create(self, request: UpsertWorkspaceSkillRequest) -> WorkspaceSkillItem:
        validated = self._snapshot_request(request)
        return self._repository.create(
            workspace_id=request.workspace_id,
            name=request.name,
            capability_family=request.capability_family,
            engine_scope=self._normalized_engine_scope(request),
            priority=request.priority,
            source_kind=request.source_kind,
            source_url=validated.source_url,
            status=request.status,
            validation_status=validated.validation_status,
            visible_to_default_assistant=request.visible_to_default_assistant,
            title=validated.title,
            summary=validated.summary,
            content_markdown=validated.content_markdown,
            content_bundle=validated.content_bundle.model_dump(mode="json"),
            content_sha256=validated.content_sha256,
            version_digest=validated.version_digest,
            last_validated_at=validated.last_validated_at,
        )

    def update(
        self,
        *,
        skill_id: str,
        workspace_id: str,
        request: UpsertWorkspaceSkillRequest,
    ) -> WorkspaceSkillItem | None:
        validated = self._snapshot_request(request)
        return self._repository.update(
            skill_id=skill_id,
            workspace_id=workspace_id,
            name=request.name,
            capability_family=request.capability_family,
            engine_scope=self._normalized_engine_scope(request),
            priority=request.priority,
            source_kind=request.source_kind,
            source_url=validated.source_url,
            status=request.status,
            validation_status=validated.validation_status,
            visible_to_default_assistant=request.visible_to_default_assistant,
            title=validated.title,
            summary=validated.summary,
            content_markdown=validated.content_markdown,
            content_bundle=validated.content_bundle.model_dump(mode="json"),
            content_sha256=validated.content_sha256,
            version_digest=validated.version_digest,
            last_validated_at=validated.last_validated_at,
        )

    def delete(self, *, skill_id: str, workspace_id: str) -> bool:
        return self._repository.delete(skill_id=skill_id, workspace_id=workspace_id)

    def _snapshot_request(
        self,
        request: UpsertWorkspaceSkillRequest,
    ) -> ValidatedWorkspaceSkillPayload:
        if isinstance(request, UpsertGithubWorkspaceSkillRequest):
            return self._validate_and_snapshot_github(request.source_url)
        if isinstance(request, UpsertGitLabWorkspaceSkillRequest):
            return self._validate_and_snapshot_gitlab(request)
        if isinstance(request, UpsertManualWorkspaceSkillRequest):
            return self._validate_and_snapshot_manual(
                name=request.name,
                markdown=request.content_markdown,
            )
        raise WorkspaceSkillValidationError("workspace_skill_unsupported_source_kind")

    def _validate_and_snapshot_gitlab(
        self,
        request: UpsertGitLabWorkspaceSkillRequest,
    ) -> ValidatedWorkspaceSkillPayload:
        if self._gitlab_connector_repository is None:
            raise WorkspaceSkillValidationError("workspace_skill_gitlab_connector_unavailable")
        connection = self._gitlab_connector_repository.get_connection(
            workspace_id=request.workspace_id,
            name=request.connector_name,
        )
        if connection is None:
            raise WorkspaceSkillValidationError("workspace_skill_gitlab_connector_not_found")
        project_path = request.project_path.strip().strip("/")
        bundle_path = self._safe_bundle_path(request.path)
        if not project_path:
            raise WorkspaceSkillValidationError("workspace_skill_gitlab_project_invalid")
        if bundle_path is None:
            raise WorkspaceSkillValidationError("workspace_skill_gitlab_path_invalid")
        main_path = self._gitlab_main_path(bundle_path)
        try:
            commit_sha = self._gitlab_service.resolve_commit(
                connection,
                project_path=project_path,
                ref=request.ref.strip(),
            )
            provenance_url = (
                f"gitlab://{request.connector_name}/{project_path}@{commit_sha}/{bundle_path}"
            )
            tree = self._gitlab_service.list_tree(
                connection,
                project_path=project_path,
                path=bundle_path or None,
                ref=commit_sha,
                recursive=True,
                limit=self._MAX_BUNDLE_ASSETS,
            )
        except GitLabRequestError as exc:
            raise WorkspaceSkillValidationError("workspace_skill_gitlab_import_failed") from exc
        if tree.get("truncated"):
            raise WorkspaceSkillValidationError("workspace_skill_gitlab_bundle_too_many_files")
        assets: list[WorkspaceSkillAsset] = []
        total_bytes = 0
        for entry in tree.get("items", []):
            if (
                not isinstance(entry, dict)
                or entry.get("type") != "blob"
                or entry.get("mode") == "120000"
            ):
                continue
            path = entry.get("path")
            if not isinstance(path, str) or not path.lower().endswith((".md", ".txt")):
                continue
            relative_path = self._safe_bundle_path(path)
            if relative_path is None or not self._is_within_bundle(
                path=relative_path, bundle_path=bundle_path
            ):
                continue
            try:
                file = self._gitlab_service.read_file(
                    connection,
                    project_path=project_path,
                    file_path=relative_path,
                    ref=commit_sha,
                    max_tokens=4000,
                )
            except GitLabRequestError as exc:
                raise WorkspaceSkillValidationError("workspace_skill_gitlab_import_failed") from exc
            if file.get("truncated"):
                raise WorkspaceSkillValidationError("workspace_skill_gitlab_file_too_large")
            markdown = file.get("content")
            if not isinstance(markdown, str):
                raise WorkspaceSkillValidationError("workspace_skill_gitlab_file_invalid")
            encoded = markdown.encode("utf-8")
            if total_bytes + len(encoded) > self._MAX_BUNDLE_BYTES:
                raise WorkspaceSkillValidationError("workspace_skill_gitlab_bundle_too_large")
            total_bytes += len(encoded)
            assets.append(
                self._build_asset(
                    source_url=(
                        file["url"]
                        if isinstance(file.get("url"), str)
                        else provenance_url
                    ),
                    path=relative_path,
                    markdown=markdown,
                    content=encoded,
                    main_path=main_path,
                    skill_dir=bundle_path,
                )
            )
        if not assets:
            raise WorkspaceSkillValidationError("workspace_skill_gitlab_bundle_empty")
        main_asset = next((asset for asset in assets if asset.path == main_path), None)
        if main_asset is None:
            raise WorkspaceSkillValidationError("workspace_skill_gitlab_main_file_missing")
        assets = [
            main_asset,
            *sorted((asset for asset in assets if asset.path != main_path), key=lambda asset: asset.path),
        ]
        bundle = WorkspaceSkillBundle(assets=assets)
        title, summary = self._extract_title_and_summary(main_asset.content_markdown)
        return ValidatedWorkspaceSkillPayload(
            source_url=provenance_url,
            title=title,
            summary=summary,
            content_markdown=main_asset.content_markdown,
            content_bundle=bundle,
            content_sha256=main_asset.content_sha256 or "",
            version_digest=self._bundle_digest(bundle),
            validation_status="valid",
            last_validated_at=datetime.now(tz=UTC),
        )

    @staticmethod
    def _safe_bundle_path(value: str) -> str | None:
        candidate = value.strip().strip("/")
        if not candidate:
            return ""
        if "\x00" in candidate:
            return None
        path = PurePosixPath(candidate)
        if path.is_absolute() or ".." in path.parts or str(path) == ".":
            return None
        return str(path) if candidate else ""

    @staticmethod
    def _gitlab_main_path(bundle_path: str) -> str:
        return f"{bundle_path}/SKILL.md" if bundle_path else "SKILL.md"

    @staticmethod
    def _is_within_bundle(*, path: str, bundle_path: str) -> bool:
        if not bundle_path:
            return True
        return path == bundle_path or path.startswith(f"{bundle_path}/")

    @staticmethod
    def _normalized_engine_scope(request: BaseUpsertWorkspaceSkillRequest) -> str:
        if request.capability_family == "chat_instruction":
            return "any"
        return request.engine_scope

    @classmethod
    def _normalize_source_url(cls, raw_url: str) -> str:
        url = raw_url.strip()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc not in {
            "github.com",
            "raw.githubusercontent.com",
        }:
            raise WorkspaceSkillValidationError("workspace_skill_invalid_source_url")

        if parsed.netloc == "github.com":
            repo_match = _GITHUB_REPO_RE.match(parsed.path)
            if repo_match is not None:
                owner, repo = repo_match.groups()
                known_skill_path = cls._known_github_skill_path(owner=owner, repo=repo)
                if known_skill_path is not None:
                    return urlunsplit((
                        "https",
                        "raw.githubusercontent.com",
                        f"/{owner}/{repo}/main/{known_skill_path}",
                        "",
                        "",
                    ))

            tree_match = _GITHUB_TREE_RE.match(parsed.path)
            if tree_match is not None:
                owner, repo, ref, tree_path = tree_match.groups()
                normalized_tree_path = (tree_path or "").strip("/")
                known_skill_path = cls._known_github_skill_path(owner=owner, repo=repo)
                allowed_tree_paths = cls._known_github_tree_paths(known_skill_path)
                if known_skill_path is not None and normalized_tree_path in allowed_tree_paths:
                    return urlunsplit((
                        "https",
                        "raw.githubusercontent.com",
                        f"/{owner}/{repo}/{ref}/{known_skill_path}",
                        "",
                        "",
                    ))

            match = _GITHUB_BLOB_RE.match(parsed.path)
            if match is None:
                raise WorkspaceSkillValidationError("workspace_skill_invalid_source_url")
            owner, repo, ref, path = match.groups()
            return urlunsplit((
                "https",
                "raw.githubusercontent.com",
                f"/{owner}/{repo}/{ref}/{path}",
                "",
                "",
            ))

        if _RAW_GITHUB_RE.match(parsed.path) is None:
            raise WorkspaceSkillValidationError("workspace_skill_invalid_source_url")
        if not parsed.path.lower().endswith(".md"):
            raise WorkspaceSkillValidationError("workspace_skill_invalid_source_url")
        return urlunsplit(("https", parsed.netloc, parsed.path, "", ""))

    @staticmethod
    def _known_github_skill_path(*, owner: str, repo: str) -> str | None:
        return _KNOWN_GITHUB_SKILL_REPOS.get((owner.lower(), repo.lower()))

    @staticmethod
    def _known_github_tree_paths(skill_path: str | None) -> set[str]:
        if skill_path is None:
            return set()
        skill_dir = str(PurePosixPath(skill_path).parent)
        return {"", "skills", skill_dir}

    def _validate_and_snapshot_github(self, raw_url: str) -> ValidatedWorkspaceSkillPayload:
        source_url = self._normalize_source_url(raw_url)
        owner, repo, ref, path = self._parse_raw_github_source(source_url)
        markdown, content = self._fetch_markdown(source_url)
        title, summary = self._extract_title_and_summary(markdown)
        bundle_assets = [
            self._build_asset(
                source_url=source_url,
                path=path,
                markdown=markdown,
                content=content,
                main_path=path,
                skill_dir=str(PurePosixPath(path).parent),
            )
        ]
        related_assets = self._resolve_related_assets(
            owner=owner,
            repo=repo,
            ref=ref,
            main_path=path,
        )
        total_bundle_bytes = len(content)
        for asset in related_assets:
            asset_bytes = len(asset.content_markdown.encode("utf-8"))
            if total_bundle_bytes + asset_bytes > self._MAX_BUNDLE_BYTES:
                break
            bundle_assets.append(asset)
            total_bundle_bytes += asset_bytes

        sha256 = hashlib.sha256(content).hexdigest()
        bundle = WorkspaceSkillBundle(assets=bundle_assets)
        return ValidatedWorkspaceSkillPayload(
            source_url=source_url,
            title=title,
            summary=summary,
            content_markdown=markdown,
            content_bundle=bundle,
            content_sha256=sha256,
            version_digest=self._bundle_digest(bundle),
            validation_status="valid",
            last_validated_at=datetime.now(tz=UTC),
        )

    def _validate_and_snapshot_manual(
        self,
        *,
        name: str,
        markdown: str,
    ) -> ValidatedWorkspaceSkillPayload:
        content = markdown.encode("utf-8")
        if len(content) > self._MAX_MANUAL_CONTENT_BYTES:
            raise WorkspaceSkillValidationError("workspace_skill_content_too_large")
        if "\x00" in markdown:
            raise WorkspaceSkillValidationError("workspace_skill_binary_content")
        if not markdown.strip():
            raise WorkspaceSkillValidationError("workspace_skill_empty_content")

        asset = self._build_manual_asset(name=name, markdown=markdown, content=content)
        bundle = WorkspaceSkillBundle(assets=[asset])
        return ValidatedWorkspaceSkillPayload(
            source_url=None,
            title=asset.title,
            summary=asset.summary,
            content_markdown=markdown,
            content_bundle=bundle,
            content_sha256=hashlib.sha256(content).hexdigest(),
            version_digest=self._bundle_digest(bundle),
            validation_status="valid",
            last_validated_at=datetime.now(tz=UTC),
        )

    @staticmethod
    def _bundle_digest(bundle: WorkspaceSkillBundle) -> str:
        """Hash the complete persisted bundle, not only its primary markdown file."""
        canonical = json.dumps(
            bundle.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _resolve_related_assets(
        self,
        *,
        owner: str,
        repo: str,
        ref: str,
        main_path: str,
    ) -> list[WorkspaceSkillAsset]:
        main_name = PurePosixPath(main_path).name
        if main_name != "SKILL.md":
            return []

        skill_dir = str(PurePosixPath(main_path).parent)
        tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1"
        try:
            response = self._http_client.get(tree_url)
            if response.status_code >= 400:
                return []
            payload = response.json()
        except Exception:
            return []

        tree = payload.get("tree")
        if not isinstance(tree, list):
            return []

        markdown_paths: list[str] = []
        prefix = f"{skill_dir}/"
        for item in tree:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "blob":
                continue
            asset_path = str(item.get("path") or "").strip()
            if not asset_path.startswith(prefix):
                continue
            if asset_path == main_path or not asset_path.lower().endswith(".md"):
                continue
            if PurePosixPath(asset_path).name.startswith("_"):
                continue
            markdown_paths.append(asset_path)

        def sort_key(asset_path: str) -> tuple[int, str]:
            kind = self._asset_kind(path=asset_path, main_path=main_path, skill_dir=skill_dir)
            priority = {"compiled": 0, "rule": 1, "doc": 2}.get(kind, 9)
            return (priority, asset_path)

        assets: list[WorkspaceSkillAsset] = []
        for asset_path in sorted(markdown_paths, key=sort_key)[: self._MAX_BUNDLE_ASSETS]:
            asset_url = urlunsplit((
                "https",
                "raw.githubusercontent.com",
                f"/{owner}/{repo}/{ref}/{asset_path}",
                "",
                "",
            ))
            try:
                markdown, content = self._fetch_markdown(asset_url)
            except WorkspaceSkillValidationError:
                continue
            assets.append(
                self._build_asset(
                    source_url=asset_url,
                    path=asset_path,
                    markdown=markdown,
                    content=content,
                    main_path=main_path,
                    skill_dir=skill_dir,
                )
            )
        return assets

    def _fetch_markdown(self, source_url: str) -> tuple[str, bytes]:
        response = self._http_client.get(source_url)
        if response.status_code >= 400:
            raise WorkspaceSkillValidationError("workspace_skill_fetch_failed")

        content = response.content
        if len(content) > self._MAX_CONTENT_BYTES:
            raise WorkspaceSkillValidationError("workspace_skill_content_too_large")
        if b"\x00" in content:
            raise WorkspaceSkillValidationError("workspace_skill_binary_content")

        content_type = str(response.headers.get("content-type") or "").lower()
        is_markdown_content = any(
            token in content_type for token in ("text/plain", "text/markdown", "text/x-markdown")
        )
        if content_type and not is_markdown_content and "application/octet-stream" in content_type:
            raise WorkspaceSkillValidationError("workspace_skill_binary_content")

        try:
            markdown = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceSkillValidationError("workspace_skill_invalid_encoding") from exc

        if not markdown.strip():
            raise WorkspaceSkillValidationError("workspace_skill_empty_content")
        return markdown, content

    def _build_asset(
        self,
        *,
        source_url: str,
        path: str,
        markdown: str,
        content: bytes,
        main_path: str,
        skill_dir: str,
    ) -> WorkspaceSkillAsset:
        frontmatter, body = self._parse_frontmatter(markdown)
        title, summary = self._extract_title_and_summary(markdown)
        title = str(frontmatter.get("title") or title or "").strip() or None
        summary = str(frontmatter.get("impactDescription") or summary or "").strip()[:500] or None
        tags = frontmatter.get("tags")
        tag_values = (
            [str(item).strip() for item in tags if str(item).strip()]
            if isinstance(tags, list)
            else []
        )
        rule_id = None
        kind = self._asset_kind(path=path, main_path=main_path, skill_dir=skill_dir)
        if kind == "rule":
            rule_id = PurePosixPath(path).stem
        return WorkspaceSkillAsset(
            path=path,
            source_url=source_url,
            kind=kind,
            title=title,
            summary=summary or self._extract_title_and_summary(body)[1],
            rule_id=rule_id,
            impact=str(frontmatter.get("impact") or "").strip() or None,
            tags=tag_values,
            content_markdown=markdown,
            content_sha256=hashlib.sha256(content).hexdigest(),
            frontmatter=frontmatter,
        )

    def _build_manual_asset(
        self,
        *,
        name: str,
        markdown: str,
        content: bytes,
    ) -> WorkspaceSkillAsset:
        frontmatter, body = self._parse_frontmatter(markdown)
        title, summary = self._extract_title_and_summary(markdown)
        title = str(frontmatter.get("title") or title or "").strip() or None
        summary = (
            str(
                frontmatter.get("use_when")
                or frontmatter.get("usage_description")
                or frontmatter.get("summary")
                or summary
                or ""
            ).strip()[:500]
            or None
        )
        return WorkspaceSkillAsset(
            path=f"manual/{name.strip() or 'skill'}.md",
            source_url=None,
            kind="main",
            title=title,
            summary=summary or self._extract_title_and_summary(body)[1],
            content_markdown=markdown,
            content_sha256=hashlib.sha256(content).hexdigest(),
            frontmatter=frontmatter,
        )

    @staticmethod
    def _parse_raw_github_source(source_url: str) -> tuple[str, str, str, str]:
        parsed = urlsplit(source_url)
        match = _RAW_GITHUB_RE.match(parsed.path)
        if match is None:
            raise WorkspaceSkillValidationError("workspace_skill_invalid_source_url")
        return match.groups()

    @staticmethod
    def _asset_kind(*, path: str, main_path: str, skill_dir: str) -> str:
        if path == main_path:
            return "main"
        asset_name = PurePosixPath(path).name
        if asset_name == "AGENTS.md":
            return "compiled"
        if path.startswith(f"{skill_dir}/rules/"):
            return "rule"
        return "doc"

    @staticmethod
    def _parse_frontmatter(markdown: str) -> tuple[dict[str, object], str]:
        lines = markdown.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, markdown
        closing_index = next(
            (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        if closing_index is None:
            return {}, markdown

        frontmatter: dict[str, object] = {}
        for line in lines[1:closing_index]:
            stripped = line.strip()
            if not stripped or ":" not in stripped:
                continue
            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            if not key:
                continue
            if value.startswith("[") and value.endswith("]"):
                items = [
                    item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()
                ]
                frontmatter[key] = items
                continue
            frontmatter[key] = value.strip("'\"")

        body = "\n".join(lines[closing_index + 1 :])
        return frontmatter, body

    @staticmethod
    def _extract_title_and_summary(markdown: str) -> tuple[str | None, str | None]:
        lines = [line.strip() for line in markdown.splitlines()]
        if lines and lines[0] == "---":
            closing_index = next(
                (index for index, line in enumerate(lines[1:], start=1) if line == "---"), None
            )
            if closing_index is not None:
                lines = lines[closing_index + 1 :]
        title: str | None = None
        summary: str | None = None
        for line in lines:
            if not line:
                continue
            if title is None and line.startswith("#"):
                title = line.lstrip("#").strip()[:255] or None
                continue
            if line.startswith(("-", "*", ">")):
                continue
            clean = _MARKDOWN_LINK_RE.sub(r"\1", line)
            summary = clean[:500] or None
            if summary:
                break
        return title, summary
