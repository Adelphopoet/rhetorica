from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit, urlunsplit

import httpx
from rhetorica_backend.contracts.workspace_skills import (
    UpsertWorkspaceSkillRequest,
    WorkspaceSkillAsset,
    WorkspaceSkillBundle,
    WorkspaceSkillItem,
)
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
    source_url: str
    title: str | None
    summary: str | None
    content_markdown: str
    content_bundle: WorkspaceSkillBundle
    content_sha256: str
    validation_status: str
    last_validated_at: datetime


class WorkspaceSkillService:
    """Validates, snapshots, and persists workspace skills."""

    _MAX_CONTENT_BYTES = 262_144
    _MAX_BUNDLE_BYTES = 1_048_576
    _MAX_BUNDLE_ASSETS = 64

    def __init__(
        self,
        repository: WorkspaceSkillRepository,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._repository = repository
        self._http_client = http_client or httpx.Client(timeout=15.0, follow_redirects=True)

    def list(self, workspace_id: str, limit: int) -> list[WorkspaceSkillItem]:
        return self._repository.list(workspace_id=workspace_id, limit=limit)

    def create(self, request: UpsertWorkspaceSkillRequest) -> WorkspaceSkillItem:
        validated = self._validate_and_snapshot(request.source_url)
        return self._repository.create(
            workspace_id=request.workspace_id,
            name=request.name,
            capability_family=request.capability_family,
            engine_scope=request.engine_scope,
            priority=request.priority,
            source_kind=request.source_kind,
            source_url=validated.source_url,
            status=request.status,
            validation_status=validated.validation_status,
            title=validated.title,
            summary=validated.summary,
            content_markdown=validated.content_markdown,
            content_bundle=validated.content_bundle.model_dump(mode="json"),
            content_sha256=validated.content_sha256,
            last_validated_at=validated.last_validated_at,
        )

    def update(
        self,
        *,
        skill_id: str,
        workspace_id: str,
        request: UpsertWorkspaceSkillRequest,
    ) -> WorkspaceSkillItem | None:
        validated = self._validate_and_snapshot(request.source_url)
        return self._repository.update(
            skill_id=skill_id,
            workspace_id=workspace_id,
            name=request.name,
            capability_family=request.capability_family,
            engine_scope=request.engine_scope,
            priority=request.priority,
            source_kind=request.source_kind,
            source_url=validated.source_url,
            status=request.status,
            validation_status=validated.validation_status,
            title=validated.title,
            summary=validated.summary,
            content_markdown=validated.content_markdown,
            content_bundle=validated.content_bundle.model_dump(mode="json"),
            content_sha256=validated.content_sha256,
            last_validated_at=validated.last_validated_at,
        )

    def delete(self, *, skill_id: str, workspace_id: str) -> bool:
        return self._repository.delete(skill_id=skill_id, workspace_id=workspace_id)

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
                    return urlunsplit(
                        (
                            "https",
                            "raw.githubusercontent.com",
                            f"/{owner}/{repo}/main/{known_skill_path}",
                            "",
                            "",
                        )
                    )

            tree_match = _GITHUB_TREE_RE.match(parsed.path)
            if tree_match is not None:
                owner, repo, ref, tree_path = tree_match.groups()
                normalized_tree_path = (tree_path or "").strip("/")
                known_skill_path = cls._known_github_skill_path(owner=owner, repo=repo)
                allowed_tree_paths = cls._known_github_tree_paths(known_skill_path)
                if known_skill_path is not None and normalized_tree_path in allowed_tree_paths:
                    return urlunsplit(
                        (
                            "https",
                            "raw.githubusercontent.com",
                            f"/{owner}/{repo}/{ref}/{known_skill_path}",
                            "",
                            "",
                        )
                    )

            match = _GITHUB_BLOB_RE.match(parsed.path)
            if match is None:
                raise WorkspaceSkillValidationError("workspace_skill_invalid_source_url")
            owner, repo, ref, path = match.groups()
            return urlunsplit(
                ("https", "raw.githubusercontent.com", f"/{owner}/{repo}/{ref}/{path}", "", "")
            )

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

    def _validate_and_snapshot(self, raw_url: str) -> ValidatedWorkspaceSkillPayload:
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
        return ValidatedWorkspaceSkillPayload(
            source_url=source_url,
            title=title,
            summary=summary,
            content_markdown=markdown,
            content_bundle=WorkspaceSkillBundle(assets=bundle_assets),
            content_sha256=sha256,
            validation_status="valid",
            last_validated_at=datetime.now(tz=UTC),
        )

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
            asset_url = urlunsplit(
                (
                    "https",
                    "raw.githubusercontent.com",
                    f"/{owner}/{repo}/{ref}/{asset_path}",
                    "",
                    "",
                )
            )
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
        summary = (
            str(frontmatter.get("impactDescription") or summary or "").strip()[:500] or None
        )
        tags = frontmatter.get("tags")
        tag_values = [
            str(item).strip()
            for item in tags
            if str(item).strip()
        ] if isinstance(tags, list) else []
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
        if asset_name == "GUIDE.md":
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
                    item.strip().strip("'\"")
                    for item in value[1:-1].split(",")
                    if item.strip()
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
            closing_index = next((index for index, line in enumerate(lines[1:], start=1) if line == "---"), None)
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
