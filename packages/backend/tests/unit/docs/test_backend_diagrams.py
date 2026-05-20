from pathlib import Path

from rhetorica_backend.docs.backend_diagrams import (
    DEFAULT_DOMAIN_SPECS,
    find_repo_root,
    generate_domain_diagrams,
)


def test_generate_execution_domain_diagrams(tmp_path: Path) -> None:
    repo_root = find_repo_root(Path(__file__))
    domain_spec = next(spec for spec in DEFAULT_DOMAIN_SPECS if spec.slug == "domain-execution")

    artifacts = generate_domain_diagrams(
        domain_spec,
        repo_root=repo_root,
        output_root=tmp_path,
    )

    assert artifacts.class_diagram_path.exists()
    assert artifacts.package_diagram_path.exists()
    assert artifacts.index_path.exists()

    assert "classDiagram" in artifacts.class_diagram_path.read_text(encoding="utf-8")
    assert "flowchart TB" in artifacts.package_diagram_path.read_text(encoding="utf-8")
    assert "```mermaid" in artifacts.index_path.read_text(encoding="utf-8")
