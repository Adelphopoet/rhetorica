#!/usr/bin/env python3
"""Normalize and validate Docsy metadata for canonical Rhetorica OpenSpec specs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SPEC_RELATIVE_PATHS = {
    "FR": ("requirements", "functional"),
    "NFR": ("requirements", "non-functional"),
    "UC": ("use-cases",),
}

URL_BASES = {
    "FR": "/docs/rhetorica/fr",
    "NFR": "/docs/rhetorica/nfr",
    "UC": "/docs/rhetorica/uc",
}

ID_PATTERNS = {
    "FR": re.compile(r"FR-RH-\d{3}(?:\.\d+)*"),
    "NFR": re.compile(r"NFR-RH-\d{3}(?:\.\d+)*"),
    "UC": re.compile(r"UC-RH-\d+"),
}

REQUIREMENT_HEADER_RE = re.compile(
    r"^### Requirement:\s+"
    r"(?P<id>(?:FR|NFR)-RH-\d{3}(?:\.\d+)*|UC-RH-\d+)"
    r"\s+[—-]\s+(?P<title>[^\n]+?)\s*$",
    re.MULTILINE,
)
USE_CASES_RE = re.compile(r"^\*\*Use Cases:\*\*\s*(?P<ids>[^\n]+)$", re.MULTILINE)
SCENARIO_RE = re.compile(r"^#### Scenario:\s+.+$", re.MULTILINE)
WHEN_RE = re.compile(r"^- \*\*WHEN\*\*\s+.+$", re.MULTILINE)
THEN_RE = re.compile(r"^- \*\*THEN\*\*\s+.+$", re.MULTILINE)
ANCHOR_RE = re.compile(
    r"^<a id=\"(?:fr|nfr)-rh-\d{3}(?:-\d+)*\"></a>\n"
    r"|^<a id=\"uc-rh-\d+\"></a>\n",
    re.MULTILINE,
)
PLANTUML_RE = re.compile(r"```plantuml\s+.*?```", re.DOTALL)
USE_CASE_INDEX = Path("docs/rhetorica/uc/_index.md")


@dataclass(frozen=True)
class Requirement:
    id: str
    title: str
    block: str


@dataclass(frozen=True)
class SpecDocument:
    path: Path
    kind: str
    slug: str
    body: str
    requirements: tuple[Requirement, ...]

    @property
    def root(self) -> Requirement:
        return self.requirements[0]


def _split_front_matter(content: str) -> tuple[str, str]:
    if not content.startswith("---\n"):
        return "", content
    end = content.find("\n---\n", 4)
    if end == -1:
        return "", content
    return content[4:end], content[end + 5 :]


def _kind_for_path(spec_root: Path, path: Path) -> tuple[str, str] | None:
    parts = path.relative_to(spec_root).parts
    for kind, prefix in SPEC_RELATIVE_PATHS.items():
        if parts[: len(prefix)] == prefix and len(parts) == len(prefix) + 2 and parts[-1] == "spec.md":
            return kind, parts[-2]
    return None


def _requirements(body: str) -> tuple[Requirement, ...]:
    matches = list(REQUIREMENT_HEADER_RE.finditer(body))
    result: list[Requirement] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        result.append(
            Requirement(
                id=match.group("id"),
                title=match.group("title").strip(),
                block=body[match.end() : end],
            )
        )
    return tuple(result)


def load_specs(repo_root: Path) -> tuple[list[SpecDocument], list[str]]:
    spec_root = repo_root / "openspec" / "specs"
    errors: list[str] = []
    documents: list[SpecDocument] = []
    if not spec_root.is_dir():
        return [], [f"Missing canonical spec directory: {spec_root}"]

    for path in sorted(spec_root.rglob("spec.md")):
        placement = _kind_for_path(spec_root, path)
        if placement is None:
            errors.append(f"Unsupported spec location: {path.relative_to(repo_root)}")
            continue
        kind, slug = placement
        _, body = _split_front_matter(path.read_text(encoding="utf-8"))
        requirements = _requirements(body)
        if not requirements:
            errors.append(f"No Requirement blocks in {path.relative_to(repo_root)}")
            continue
        documents.append(SpecDocument(path, kind, slug, body, requirements))

    for kind in SPEC_RELATIVE_PATHS:
        if not any(document.kind == kind for document in documents):
            errors.append(f"No {kind} specs found under openspec/specs")
    return documents, errors


def _numeric_id_key(requirement_id: str) -> tuple[int, ...]:
    if requirement_id.startswith("UC-RH-"):
        return (int(requirement_id.rsplit("-", 1)[1]),)
    return tuple(int(part) for part in requirement_id.rsplit("-", 1)[1].split("."))


def _parse_use_cases(requirement: Requirement) -> tuple[str, ...]:
    match = USE_CASES_RE.search(requirement.block)
    if not match:
        return ()
    return tuple(item.strip().replace("`", "") for item in match.group("ids").split(",") if item.strip())


def _validate_shared_diagram(repo_root: Path, uc_ids: set[str]) -> list[str]:
    path = repo_root / USE_CASE_INDEX
    relative = path.relative_to(repo_root).as_posix()
    if not path.is_file():
        return [f"Missing use-case section index: {relative}"]
    diagrams = PLANTUML_RE.findall(path.read_text(encoding="utf-8"))
    if len(diagrams) != 1:
        return [f"{relative}: expected exactly one fenced plantuml diagram, found {len(diagrams)}"]
    diagram = diagrams[0]
    return [f"{relative}: shared diagram does not mention {uc_id}" for uc_id in sorted(uc_ids, key=_numeric_id_key) if uc_id not in diagram]


def validate_specs(repo_root: Path, documents: list[SpecDocument]) -> list[str]:
    errors: list[str] = []
    seen: dict[str, Path] = {}
    uc_ids = {document.root.id for document in documents if document.kind == "UC"}
    errors.extend(_validate_shared_diagram(repo_root, uc_ids))

    for document in documents:
        relative = document.path.relative_to(repo_root).as_posix()
        root_id = document.root.id
        expected_slug = root_id.lower().replace(".", "-")
        if document.slug != expected_slug:
            errors.append(f"{relative}: directory must be {expected_slug!r}")
        if not ID_PATTERNS[document.kind].fullmatch(root_id):
            errors.append(f"{relative}: invalid root ID {root_id!r}")
        if document.kind in {"FR", "NFR"} and "." in root_id:
            errors.append(f"{relative}: root requirement cannot be a child ID")
        if document.kind == "UC" and len(document.requirements) != 1:
            errors.append(f"{relative}: a use-case capability must contain exactly one requirement")

        local_ids = {requirement.id for requirement in document.requirements}
        for requirement in document.requirements:
            if requirement.id in seen:
                errors.append(f"Duplicate ID {requirement.id}: {seen[requirement.id]} and {document.path}")
            seen[requirement.id] = document.path
            if not SCENARIO_RE.search(requirement.block) or not WHEN_RE.search(requirement.block) or not THEN_RE.search(requirement.block):
                errors.append(f"{relative}: {requirement.id} needs Scenario with WHEN and THEN")
            if document.kind in {"FR", "NFR"}:
                if requirement.id != root_id and not requirement.id.startswith(f"{root_id}."):
                    errors.append(f"{relative}: child {requirement.id} does not belong to {root_id}")
                if "." in requirement.id and requirement.id.rsplit(".", 1)[0] not in local_ids:
                    errors.append(f"{relative}: child {requirement.id} has no parent")
                references = _parse_use_cases(requirement)
                if not references:
                    errors.append(f"{relative}: {requirement.id} has no **Use Cases:** line")
                for use_case in references:
                    if use_case not in uc_ids:
                        errors.append(f"{relative}: {requirement.id} references missing {use_case}")
            if document.kind == "NFR":
                for field in ("Метрика", "Порог", "Условия измерения", "Проверка"):
                    if f"**{field}:**" not in requirement.block:
                        errors.append(f"{relative}: {requirement.id} has no **{field}:** field")
            if document.kind == "UC":
                for field in ("Основной актор", "Цель"):
                    if f"**{field}:**" not in requirement.block:
                        errors.append(f"{relative}: {requirement.id} has no **{field}:** field")
    return errors


def _expected_front_matter(document: SpecDocument) -> str:
    root_id = document.root.id
    weight = _numeric_id_key(root_id)[0]
    title = json.dumps(document.root.title, ensure_ascii=False)
    url = f"{URL_BASES[document.kind]}/{document.slug}/"
    return (
        "---\n"
        f"title: {title}\n"
        f"linkTitle: {root_id}\n"
        f"weight: {weight}\n"
        f"url: {url}\n"
        f"openspec_kind: {document.kind}\n"
        f"requirement_root: {root_id}\n"
        "---\n\n"
    )


def _normalize_anchors(body: str) -> str:
    body = ANCHOR_RE.sub("", body)

    def add_anchor(match: re.Match[str]) -> str:
        anchor = match.group("id").lower().replace(".", "-")
        return f'{match.group(0)}\n<a id="{anchor}"></a>'

    return REQUIREMENT_HEADER_RE.sub(add_anchor, body)


def sync_repository(repo_root: Path, *, write: bool) -> tuple[list[Path], list[str]]:
    documents, errors = load_specs(repo_root)
    errors.extend(validate_specs(repo_root, documents))
    if errors:
        return [], errors
    changed: list[Path] = []
    for document in documents:
        expected = _expected_front_matter(document) + _normalize_anchors(document.body).lstrip("\n").rstrip() + "\n"
        if document.path.read_text(encoding="utf-8") == expected:
            continue
        changed.append(document.path)
        if write:
            document.path.write_text(expected, encoding="utf-8")
    if changed and not write:
        errors.extend(f"Docsy metadata is stale: {path.relative_to(repo_root)}" for path in changed)
    return changed, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    changed, errors = sync_repository(args.repo_root.resolve(), write=args.write)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    total = sum(1 for _ in (args.repo_root / "openspec" / "specs").rglob("spec.md"))
    print(f"{'Normalized' if args.write else 'Validated'} {len(changed)} changed and {total} total specs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
