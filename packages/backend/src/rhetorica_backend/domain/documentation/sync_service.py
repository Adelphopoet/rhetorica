from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

import httpx
from rhetorica_backend.contracts.admin_integrations import DocumentationConnectorItem
from rhetorica_backend.domain.documentation.embedding import (
    DocumentationEmbeddingProvider,
    OpenAiCompatibleEmbeddingProvider,
)
from rhetorica_backend.domain.documentation.models import (
    DocumentationPageDocument,
    DocumentationSyncResult,
)
from rhetorica_backend.infra.postgres.documentation_connector_repository import (
    DocumentationConnectorRepository,
)
from rhetorica_backend.infra.qdrant.documentation_store import QdrantDocumentationStore

HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


class DocumentationSyncService:
    """Synchronize Docsy/Hugo documentation into PG and Qdrant."""

    _DEFAULT_INDEX_CANDIDATES = (
        "/llm-docs-index.json",
        "/index.json",
    )
    _MAX_CHUNK_WORDS = 120
    _CHUNK_OVERLAP_WORDS = 20
    _MAX_CHUNK_CHARS = 900
    _CHUNK_OVERLAP_CHARS = 120

    def __init__(
        self,
        repository: DocumentationConnectorRepository,
        *,
        embedding_provider_factory=None,
        qdrant_store_factory=None,
    ) -> None:
        self._repository = repository
        self._embedding_provider_factory = (
            embedding_provider_factory or OpenAiCompatibleEmbeddingProvider
        )
        self._qdrant_store_factory = qdrant_store_factory or QdrantDocumentationStore

    def sync_connector(
        self,
        *,
        connector: DocumentationConnectorItem,
        trigger: str,
    ) -> DocumentationSyncResult:
        run = self._repository.create_sync_run(connector=connector, trigger=trigger)
        warnings: list[str] = []
        pages_seen = 0
        pages_updated = 0
        chunks_indexed = 0
        try:
            connection = self._repository.resolve_secrets(
                connector.connection,
                workspace_id=connector.workspace_id,
                connector_id=connector.id,
            )
            connector = connector.model_copy(update={"connection": connection})
            documents, parser_warnings = self._load_documents(connector)
            warnings.extend(parser_warnings)
            embedder: DocumentationEmbeddingProvider | None = None
            store = None
            if connector.connection.embedding_base_url:
                embedder = self._embedding_provider_factory(connector.connection)
                store = self._qdrant_store_factory(connector.connection)
            else:
                warnings.append("documentation_embedding_base_url_missing")
            seen_refs: set[str] = set()

            for document in documents:
                if not self._path_allowed(connector, document.page_ref):
                    continue
                pages_seen += 1
                seen_refs.add(document.page_ref)
                body_hash = self._sha256(document.body_text)
                chunks = self._build_chunks(
                    connector=connector,
                    document=document,
                    body_hash=body_hash,
                )
                changed, chunks_to_index = self._repository.upsert_page_with_chunks(
                    workspace_id=connector.workspace_id,
                    connector_name=connector.name,
                    page_ref=document.page_ref,
                    url=document.url,
                    title=document.title,
                    source_path=document.source_path,
                    markdown_url=document.markdown_url,
                    summary=document.summary,
                    body_text=document.body_text,
                    body_hash=body_hash,
                    metadata_json=document.metadata,
                    chunks=chunks,
                )
                if not changed:
                    pass
                else:
                    pages_updated += 1
                if embedder is None or store is None or not chunks_to_index:
                    continue
                try:
                    store.delete_page_points(
                        workspace_id=connector.workspace_id,
                        connector_name=connector.name,
                        page_ref=document.page_ref,
                    )
                    vector_chunks: list[dict[str, Any]] = []
                    for chunk in chunks_to_index:
                        try:
                            vector = embedder.embed(chunk["content"])
                        except Exception as exc:
                            warning = self._semantic_warning(exc)
                            if warning not in warnings:
                                warnings.append(warning)
                            continue
                        vector_chunks.append({
                            **chunk,
                            "workspace_id": connector.workspace_id,
                            "connector_name": connector.name,
                            "url": document.url,
                            "title": document.title,
                            "vector": vector,
                        })
                    if vector_chunks:
                        store.upsert_chunks(vector_chunks)
                        chunks_indexed += len(vector_chunks)
                except Exception as exc:
                    warning = self._semantic_warning(exc)
                    if warning not in warnings:
                        warnings.append(warning)

            self._repository.mark_missing_pages_deleted(
                workspace_id=connector.workspace_id,
                connector_name=connector.name,
                seen_refs=seen_refs,
            )
            result = DocumentationSyncResult(
                status="succeeded",
                pages_seen=pages_seen,
                pages_updated=pages_updated,
                chunks_indexed=chunks_indexed,
                warnings=warnings,
            )
        except Exception as exc:
            result = DocumentationSyncResult(
                status="failed",
                pages_seen=pages_seen,
                pages_updated=pages_updated,
                chunks_indexed=chunks_indexed,
                warnings=warnings,
                error_message=str(exc)[:2000],
            )
        self._repository.finish_sync_run(
            run_id=run.id,
            connector=connector,
            status=result.status,
            pages_seen=result.pages_seen,
            pages_updated=result.pages_updated,
            chunks_indexed=result.chunks_indexed,
            warnings=result.warnings,
            error_message=result.error_message,
        )
        return result

    @staticmethod
    def _semantic_warning(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"documentation_semantic_index_unavailable:http_{exc.response.status_code}"
        return f"documentation_semantic_index_unavailable:{type(exc).__name__}"

    def sync_due(self, *, limit: int = 20) -> list[DocumentationSyncResult]:
        from datetime import UTC, datetime

        connectors = self._repository.list_due(now=datetime.now(tz=UTC), limit=limit)
        return [self.sync_connector(connector=connector, trigger="scheduled") for connector in connectors]

    def _load_documents(
        self,
        connector: DocumentationConnectorItem,
    ) -> tuple[list[DocumentationPageDocument], list[str]]:
        warnings: list[str] = []
        index_urls = (
            [connector.connection.index_url]
            if connector.connection.index_url
            else self._discover_index_urls(connector)
        )
        last_error: Exception | None = None
        for index_url in index_urls:
            try:
                payload = self._get_json(connector, index_url)
                documents = self._parse_index(
                    connector=connector,
                    payload=payload,
                    index_url=index_url,
                )
            except (RuntimeError, httpx.HTTPError, ValueError) as exc:
                last_error = exc
                continue
            if documents:
                return documents, warnings
            warnings.append("documentation_index_empty")
        if last_error is not None:
            raise last_error
        warnings.append("documentation_index_empty")
        return [], warnings

    def _discover_index_urls(self, connector: DocumentationConnectorItem) -> list[str]:
        urls: list[str] = []
        for candidate in self._DEFAULT_INDEX_CANDIDATES:
            url = urljoin(connector.connection.base_url.rstrip("/") + "/", candidate.lstrip("/"))
            try:
                self._get_json(connector, url)
            except (httpx.HTTPError, ValueError):
                continue
            else:
                urls.append(url)

        html_text = self._get_text(connector, connector.connection.base_url)
        match = re.search(
            r'["\']?([^"\'<>\s]*offline-search-index[^"\'<>\s]+\.json)',
            html_text,
        )
        if match:
            urls.append(self._absolute_url(connector, match.group(1)))
        if not urls:
            raise RuntimeError("documentation_index_not_found")
        return urls

    def _parse_index(
        self,
        *,
        connector: DocumentationConnectorItem,
        payload: object,
        index_url: str,
    ) -> list[DocumentationPageDocument]:
        if isinstance(payload, dict):
            raw_pages = payload.get("pages") or payload.get("documents") or payload.get("items")
            if isinstance(raw_pages, list):
                return [
                    self._parse_page_item(connector=connector, item=item, index_url=index_url)
                    for item in raw_pages
                    if isinstance(item, dict)
                ]
        if isinstance(payload, list):
            return [
                self._parse_page_item(connector=connector, item=item, index_url=index_url)
                for item in payload
                if isinstance(item, dict)
            ]
        raise RuntimeError("documentation_index_shape_invalid")

    def _parse_page_item(
        self,
        *,
        connector: DocumentationConnectorItem,
        item: dict[str, Any],
        index_url: str,
    ) -> DocumentationPageDocument:
        ref = str(item.get("ref") or item.get("page_ref") or item.get("url") or "").strip()
        title = str(item.get("title") or ref or "Untitled").strip()
        url = str(item.get("url") or ref).strip()
        url = self._absolute_url(connector, url)
        markdown_url = item.get("markdown_url")
        if isinstance(markdown_url, str):
            markdown_url = self._absolute_url(connector, markdown_url)
        body = str(item.get("body") or item.get("body_text") or item.get("markdown") or "")
        if markdown_url and not body:
            body = self._get_text(connector, markdown_url)
        if not body:
            body = str(item.get("excerpt") or item.get("description") or title)
        body = self._normalize_text(body)
        if not ref:
            ref = url
        return DocumentationPageDocument(
            page_ref=ref,
            title=title,
            url=url,
            source_path=item.get("source_path") if isinstance(item.get("source_path"), str) else None,
            markdown_url=markdown_url if isinstance(markdown_url, str) else None,
            summary=str(item.get("summary") or item.get("description") or item.get("excerpt") or "")[:2000]
            or None,
            body_text=body,
            metadata={
                "index_url": index_url,
                "tags": item.get("tags") or "",
                "categories": item.get("categories") or "",
            },
        )

    def _build_chunks(
        self,
        *,
        connector: DocumentationConnectorItem,
        document: DocumentationPageDocument,
        body_hash: str,
    ) -> list[dict[str, Any]]:
        sections = self._split_sections(document.body_text)
        chunks: list[dict[str, Any]] = []
        for section_title, section_text in sections:
            words = section_text.split()
            if not words:
                continue
            start = 0
            while start < len(words):
                part_words = words[start : start + self._MAX_CHUNK_WORDS]
                for content in self._split_oversized_chunk(" ".join(part_words).strip()):
                    chunk_index = len(chunks)
                    chunk_ref = self._sha256(
                        f"{connector.workspace_id}:{connector.name}:{document.page_ref}:{body_hash}:{chunk_index}"
                    )[:32]
                    chunks.append({
                        "page_ref": document.page_ref,
                        "chunk_ref": chunk_ref,
                        "chunk_index": chunk_index,
                        "section": section_title,
                        "content": content,
                        "content_hash": self._sha256(content),
                        "qdrant_point_id": str(uuid5(NAMESPACE_URL, chunk_ref)),
                    })
                if start + self._MAX_CHUNK_WORDS >= len(words):
                    break
                start += self._MAX_CHUNK_WORDS - self._CHUNK_OVERLAP_WORDS
        return chunks

    @classmethod
    def _split_oversized_chunk(cls, text: str) -> list[str]:
        if len(text) <= cls._MAX_CHUNK_CHARS:
            return [text] if text else []
        parts: list[str] = []
        start = 0
        while start < len(text):
            hard_end = min(len(text), start + cls._MAX_CHUNK_CHARS)
            end = hard_end
            if hard_end < len(text):
                newline = text.rfind("\n", start + 1, hard_end)
                space = text.rfind(" ", start + 1, hard_end)
                boundary = max(newline, space)
                if boundary > start + int(cls._MAX_CHUNK_CHARS * 0.65):
                    end = boundary
            part = text[start:end].strip()
            if part:
                parts.append(part)
            if end >= len(text):
                break
            next_start = max(end - cls._CHUNK_OVERLAP_CHARS, start + 1)
            while next_start < len(text) and text[next_start].isspace():
                next_start += 1
            start = next_start
        return parts

    @staticmethod
    def _split_sections(text: str) -> list[tuple[str | None, str]]:
        matches = list(HEADING_RE.finditer(text))
        if not matches:
            return [(None, text)]
        sections: list[tuple[str | None, str]] = []
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            title = match.group(2).strip()
            section_text = text[start:end].strip()
            if section_text:
                sections.append((title, section_text))
        return sections or [(None, text)]

    def _get_json(self, connector: DocumentationConnectorItem, url: str) -> object:
        response = httpx.get(
            url,
            auth=self._auth(connector),
            headers=self._headers(connector),
            timeout=60,
            verify=connector.connection.verify_tls,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json()

    def _get_text(self, connector: DocumentationConnectorItem, url: str) -> str:
        response = httpx.get(
            url,
            auth=self._auth(connector),
            headers=self._headers(connector),
            timeout=60,
            verify=connector.connection.verify_tls,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text

    @staticmethod
    def _absolute_url(connector: DocumentationConnectorItem, value: str) -> str:
        if not value:
            return value
        parsed_value = urlsplit(value)
        if parsed_value.scheme and parsed_value.netloc:
            return value
        parsed_base = urlsplit(connector.connection.base_url)
        base_root = urlunsplit((parsed_base.scheme, parsed_base.netloc, "/", "", ""))
        base_path = (parsed_base.path or "").rstrip("/")
        if value.startswith("/") and base_path and value.startswith(f"{base_path}/"):
            return urljoin(base_root, value.lstrip("/"))
        return urljoin(connector.connection.base_url.rstrip("/") + "/", value.lstrip("/"))

    @staticmethod
    def _auth(connector: DocumentationConnectorItem) -> tuple[str, str] | None:
        if connector.connection.auth_type != "basic":
            return None
        if not connector.connection.username or not connector.connection.api_token:
            return None
        return connector.connection.username, connector.connection.api_token

    @staticmethod
    def _headers(connector: DocumentationConnectorItem) -> dict[str, str]:
        if connector.connection.auth_type == "bearer" and connector.connection.api_token:
            return {"Authorization": f"Bearer {connector.connection.api_token}"}
        return {}

    @staticmethod
    def _normalize_text(value: str) -> str:
        without_tags = HTML_TAG_RE.sub(" ", value)
        lines = [WHITESPACE_RE.sub(" ", line).strip() for line in without_tags.splitlines()]
        return "\n".join(line for line in lines if line)

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _path_allowed(connector: DocumentationConnectorItem, page_ref: str) -> bool:
        include_paths = connector.connection.include_paths
        exclude_paths = connector.connection.exclude_paths
        if include_paths and not any(page_ref.startswith(prefix) for prefix in include_paths):
            return False
        return not (exclude_paths and any(page_ref.startswith(prefix) for prefix in exclude_paths))
