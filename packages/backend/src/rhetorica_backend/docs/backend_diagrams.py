from __future__ import annotations

import ast
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DomainDiagramSpec:
    slug: str
    title: str
    description: str
    python_targets: tuple[str, ...]
    package_root: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratedDiagramArtifacts:
    slug: str
    output_dir: Path
    class_diagram_path: Path
    package_diagram_path: Path
    index_path: Path


DEFAULT_DOMAIN_SPECS: tuple[DomainDiagramSpec, ...] = (
    DomainDiagramSpec(
        slug="domain-chat",
        title="Chat Domain",
        description="Governed chat orchestration, runtime context, metadata grounding, and SQL drafting.",
        python_targets=(
            "rhetorica_backend.domain.chat.settings",
            "rhetorica_backend.domain.chat.error_contract",
            "rhetorica_backend.domain.chat.conversation",
            "rhetorica_backend.domain.chat.llm",
            "rhetorica_backend.domain.chat.metadata",
            "rhetorica_backend.domain.chat.orchestration",
            "rhetorica_backend.domain.chat.sql",
        ),
        package_root="rhetorica_backend.domain.chat",
    ),
    DomainDiagramSpec(
        slug="domain-execution",
        title="Execution Domain",
        description="Guarded warehouse execution lifecycle, queue handoff, and execution state persistence.",
        python_targets=("rhetorica_backend.domain.execution",),
        package_root="rhetorica_backend.domain.execution",
    ),
    DomainDiagramSpec(
        slug="domain-rag",
        title="RAG Domain",
        description="Insight retrieval, review flow, and reusable embedding-backed feedback pipeline.",
        python_targets=("rhetorica_backend.domain.rag",),
        package_root="rhetorica_backend.domain.rag",
    ),
    DomainDiagramSpec(
        slug="domain-results",
        title="Results Domain",
        description="Result shaping, narrative document assembly, and visualization planning from execution output.",
        python_targets=("rhetorica_backend.domain.results",),
        package_root="rhetorica_backend.domain.results",
    ),
    DomainDiagramSpec(
        slug="sql-guard",
        title="SQL Guard",
        description="Read-only SQL policy enforcement and normalized guard decisions before execution.",
        python_targets=("rhetorica_backend.sql_guard",),
        package_root="rhetorica_backend.sql_guard",
        note=(
            "Runtime code lives in `rhetorica_backend.sql_guard`. "
            "The sibling `domain/sql_guard` directory is only a placeholder and is not diagrammed."
        ),
    ),
)


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "docs").exists():
            return candidate
    raise RuntimeError("Could not locate repository root from backend docs generator.")


def generate_backend_diagrams(
    *,
    repo_root: Path | None = None,
    output_root: Path | None = None,
    domain_specs: Iterable[DomainDiagramSpec] = DEFAULT_DOMAIN_SPECS,
) -> list[GeneratedDiagramArtifacts]:
    resolved_repo_root = find_repo_root(repo_root)
    resolved_output_root = output_root or resolved_repo_root / "docs" / "backend" / "generated"
    source_root = resolved_repo_root / "packages" / "backend" / "src"

    if not source_root.exists():
        raise RuntimeError(f"Backend source root does not exist: {source_root}")

    if resolved_output_root.exists():
        shutil.rmtree(resolved_output_root)
    resolved_output_root.mkdir(parents=True, exist_ok=True)

    artifacts: list[GeneratedDiagramArtifacts] = []
    for domain_spec in domain_specs:
        artifacts.append(
            generate_domain_diagrams(
                domain_spec,
                repo_root=resolved_repo_root,
                source_root=source_root,
                output_root=resolved_output_root,
            )
        )
    return artifacts


def generate_domain_diagrams(
    domain_spec: DomainDiagramSpec,
    *,
    repo_root: Path,
    source_root: Path | None = None,
    output_root: Path | None = None,
) -> GeneratedDiagramArtifacts:
    resolved_source_root = source_root or repo_root / "packages" / "backend" / "src"
    resolved_output_root = output_root or repo_root / "docs" / "backend" / "generated"
    domain_output_dir = resolved_output_root / domain_spec.slug
    domain_output_dir.mkdir(parents=True, exist_ok=True)

    class_diagram = _generate_class_diagram(domain_spec, source_root=resolved_source_root)
    package_diagram = _build_package_diagram(domain_spec, source_root=resolved_source_root)
    index_markdown = _build_domain_index_markdown(domain_spec, class_diagram=class_diagram, package_diagram=package_diagram)

    class_diagram_path = domain_output_dir / "class.mmd"
    package_diagram_path = domain_output_dir / "package.mmd"
    index_path = domain_output_dir / "index.md"
    class_diagram_path.write_text(class_diagram, encoding="utf-8")
    package_diagram_path.write_text(package_diagram, encoding="utf-8")
    index_path.write_text(index_markdown, encoding="utf-8")

    return GeneratedDiagramArtifacts(
        slug=domain_spec.slug,
        output_dir=domain_output_dir,
        class_diagram_path=class_diagram_path,
        package_diagram_path=package_diagram_path,
        index_path=index_path,
    )


def _generate_class_diagram(domain_spec: DomainDiagramSpec, *, source_root: Path) -> str:
    project_name = domain_spec.slug.replace("-", "_")
    with tempfile.TemporaryDirectory(prefix=f"rhetorica-{project_name}-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        command = [
            *_resolve_pyreverse_command(),
            "--output",
            "mmd",
            "--only-classnames",
            "--source-roots",
            str(source_root),
            "--project",
            project_name,
            *domain_spec.python_targets,
        ]
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{source_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(source_root)
        )

        completed = subprocess.run(  # noqa: S603
            command,
            cwd=temp_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "pyreverse failed for "
                f"{domain_spec.slug}:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )

        diagram_path = temp_dir / f"classes_{project_name}.mmd"
        if not diagram_path.exists():
            diagram_path = temp_dir / "classes.mmd"
        if not diagram_path.exists():
            raise RuntimeError(f"pyreverse did not create a class diagram for {domain_spec.slug}")
        return diagram_path.read_text(encoding="utf-8").strip()


def _build_package_diagram(domain_spec: DomainDiagramSpec, *, source_root: Path) -> str:
    module_paths = _discover_module_paths(domain_spec, source_root=source_root)
    ordered_modules = sorted(module_paths)
    node_ids = {module_name: f"node_{index}" for index, module_name in enumerate(ordered_modules, start=1)}
    grouped_modules: dict[str, list[str]] = defaultdict(list)
    root_modules: list[str] = []

    for module_name in ordered_modules:
        relative_module = _relative_module_name(module_name, domain_spec.package_root)
        if "." in relative_module:
            group_name, _ = relative_module.split(".", 1)
            grouped_modules[group_name].append(module_name)
            continue
        root_modules.append(module_name)

    lines = ["flowchart TB", f'  subgraph domain["{domain_spec.title}"]']
    for module_name in root_modules:
        lines.append(f'    {node_ids[module_name]}["{_module_label(module_name, domain_spec.package_root)}"]')
    for group_name in sorted(grouped_modules):
        lines.append(f'    subgraph group_{_mermaid_id(group_name)}["{group_name}"]')
        for module_name in grouped_modules[group_name]:
            lines.append(
                f'      {node_ids[module_name]}["{_module_label(module_name, domain_spec.package_root)}"]'
            )
        lines.append("    end")
    lines.append("  end")

    dependencies = _collect_internal_dependencies(module_paths, package_root=domain_spec.package_root)
    for source_module, target_module in sorted(dependencies):
        lines.append(f"  {node_ids[source_module]} --> {node_ids[target_module]}")
    return "\n".join(lines)


def _discover_module_paths(domain_spec: DomainDiagramSpec, *, source_root: Path) -> dict[str, Path]:
    module_paths: dict[str, Path] = {}
    for python_target in domain_spec.python_targets:
        target_path = source_root / Path(*python_target.split("."))
        file_target = target_path.with_suffix(".py")
        if file_target.exists():
            module_paths[python_target] = file_target
            continue
        if target_path.is_dir():
            for file_path in sorted(target_path.rglob("*.py")):
                if file_path.name == "__init__.py":
                    continue
                module_name = ".".join(file_path.relative_to(source_root).with_suffix("").parts)
                module_paths[module_name] = file_path
            continue
        raise RuntimeError(f"Could not resolve python target '{python_target}' from {source_root}")
    return module_paths


def _collect_internal_dependencies(
    module_paths: dict[str, Path], *, package_root: str
) -> set[tuple[str, str]]:
    dependencies: set[tuple[str, str]] = set()
    known_modules = frozenset(module_paths)
    for module_name, file_path in module_paths.items():
        module_ast = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for imported_module in _iter_imported_modules(module_ast, current_module=module_name):
            for resolved_target in _resolve_import_targets(
                imported_module,
                known_modules,
                package_root=package_root,
            ):
                if resolved_target != module_name:
                    dependencies.add((module_name, resolved_target))
    return dependencies


def _iter_imported_modules(module_ast: ast.AST, *, current_module: str) -> Iterable[str]:
    for node in ast.walk(module_ast):
        if isinstance(node, ast.Import):
            for imported_name in node.names:
                yield imported_name.name
        if isinstance(node, ast.ImportFrom):
            yield _resolve_import_from_module(node, current_module=current_module)


def _resolve_import_from_module(node: ast.ImportFrom, *, current_module: str) -> str:
    if node.level <= 0:
        return node.module or ""

    package_parts = current_module.split(".")[:-1]
    anchor = ".".join(package_parts)
    relative_target = "." * node.level + (node.module or "")
    return _resolve_relative_module(relative_target, anchor=anchor)


def _resolve_relative_module(relative_target: str, *, anchor: str) -> str:
    level = len(relative_target) - len(relative_target.lstrip("."))
    suffix = relative_target[level:]
    anchor_parts = anchor.split(".") if anchor else []
    if level > len(anchor_parts):
        return suffix
    resolved_parts = anchor_parts[: len(anchor_parts) - level + 1]
    if suffix:
        resolved_parts.extend(suffix.split("."))
    return ".".join(part for part in resolved_parts if part)


def _resolve_import_targets(
    imported_module: str,
    known_modules: frozenset[str],
    *,
    package_root: str,
) -> set[str]:
    if not imported_module:
        return set()
    if imported_module != package_root and not imported_module.startswith(f"{package_root}."):
        return set()

    candidates: set[str] = set()
    current_name = imported_module
    while current_name:
        if current_name in known_modules:
            candidates.add(current_name)
            break
        prefix = f"{current_name}."
        prefix_matches = {module_name for module_name in known_modules if module_name.startswith(prefix)}
        if prefix_matches:
            candidates.update(prefix_matches)
            break
        if current_name == package_root:
            break
        if "." not in current_name:
            break
        current_name = current_name.rsplit(".", 1)[0]
    return candidates


def _build_domain_index_markdown(
    domain_spec: DomainDiagramSpec,
    *,
    class_diagram: str,
    package_diagram: str,
) -> str:
    source_list = "\n".join(f"- `{target}`" for target in domain_spec.python_targets)
    note_block = f"\n> {domain_spec.note}\n" if domain_spec.note else ""
    return (
        f"# {domain_spec.title}\n\n"
        "> Generated by `uv run python scripts/generate_backend_diagrams.py`. "
        "Do not edit by hand.\n\n"
        f"{domain_spec.description}\n"
        f"{note_block}\n"
        "## Python Scope\n\n"
        f"{source_list}\n\n"
        "## Class Diagram\n\n"
        "```mermaid\n"
        f"{_format_class_diagram_for_docs(class_diagram)}\n"
        "```\n\n"
        "## Package Diagram\n\n"
        "This diagram is a generated internal module dependency map for the documented scope.\n\n"
        "```mermaid\n"
        f"{_format_flow_diagram_for_docs(package_diagram)}\n"
        "```\n"
    )


def _format_class_diagram_for_docs(diagram: str) -> str:
    stripped = diagram.strip()
    if not stripped.startswith("classDiagram"):
        return stripped

    body_lines = stripped.splitlines()[1:]
    return "\n".join(
        (
            "---",
            "config:",
            "  layout: elk",
            "  wrap: true",
            "---",
            "classDiagram",
            "  direction TB",
            *body_lines,
        )
    )


def _format_flow_diagram_for_docs(diagram: str) -> str:
    stripped = diagram.strip()
    if not stripped.startswith("flowchart"):
        return stripped

    return "\n".join(
        (
            "---",
            "config:",
            "  layout: elk",
            "  wrap: true",
            "---",
            stripped,
        )
    )


def _resolve_pyreverse_command() -> list[str]:
    pyreverse_path = shutil.which("pyreverse")
    if pyreverse_path:
        return [pyreverse_path]
    uv_path = shutil.which("uv")
    if uv_path:
        return [uv_path, "run", "--with", "pylint", "pyreverse"]
    raise RuntimeError("Could not find either 'pyreverse' or 'uv' for backend diagram generation.")


def _relative_module_name(module_name: str, package_root: str) -> str:
    prefix = f"{package_root}."
    if module_name.startswith(prefix):
        return module_name.removeprefix(prefix)
    return module_name


def _module_label(module_name: str, package_root: str) -> str:
    return _relative_module_name(module_name, package_root).replace(".", "/")


def _mermaid_id(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)
