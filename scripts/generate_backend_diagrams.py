from __future__ import annotations

from pathlib import Path

from rhetorica_backend.docs.backend_diagrams import generate_backend_diagrams


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    artifacts = generate_backend_diagrams(repo_root=repo_root)
    for artifact in artifacts:
        print(f"[backend-docs] generated {artifact.slug} -> {artifact.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
