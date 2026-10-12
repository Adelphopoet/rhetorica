from pathlib import Path

from tools.sync_openspec_docsy import sync_repository

FR_SPEC = """# SQL execution

## Purpose

Проверяемая SQL-аналитика.

## Requirements

### Requirement: FR-RH-001 — Выполнить SQL

Система SHALL выполнить разрешённый read-only SQL.

**Use Cases:** UC-RH-1

#### Scenario: Успешный запуск

- **WHEN** передан валидный запрос
- **THEN** система возвращает результат
"""

NFR_SPEC = """# SQL safety

## Purpose

Измеримая безопасность SQL.

## Requirements

### Requirement: NFR-RH-001 — Отклонять опасный SQL

Система MUST отклонять DML.

**Use Cases:** UC-RH-1

**Метрика:** доля отклонённых DML.

**Порог:** 100%.

**Условия измерения:** policy tests.

**Проверка:** unit tests.

#### Scenario: DML отклонён

- **WHEN** передан DELETE
- **THEN** guard возвращает отказ
"""

UC_SPEC = """# Execute SQL

## Purpose

Путь пользователя к результату.

## Requirements

### Requirement: UC-RH-1 — Выполнить SQL

Система SHALL поддерживать безопасное выполнение.

**Основной актор:** аналитик.

**Цель:** получить результат SQL.

#### Scenario: Успешный запуск

- **WHEN** аналитик запускает запрос
- **THEN** система возвращает результат
"""

UC_INDEX = """# UC

```plantuml
@startuml
actor Analyst
usecase "UC-RH-1: Выполнить SQL" as UC1
Analyst --> UC1
@enduml
```
"""


def _write_specs(root: Path) -> None:
    files = {
        "requirements/functional/fr-rh-001/spec.md": FR_SPEC,
        "requirements/non-functional/nfr-rh-001/spec.md": NFR_SPEC,
        "use-cases/uc-rh-1/spec.md": UC_SPEC,
    }
    for relative, content in files.items():
        path = root / "openspec" / "specs" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    index_path = root / "docs/rhetorica/uc/_index.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(UC_INDEX, encoding="utf-8")


def test_sync_adds_front_matter_and_stable_anchors(tmp_path: Path) -> None:
    _write_specs(tmp_path)

    changed, errors = sync_repository(tmp_path, write=True)

    assert not errors
    assert len(changed) == 3
    fr = (tmp_path / "openspec/specs/requirements/functional/fr-rh-001/spec.md").read_text()
    assert "openspec_kind: FR" in fr
    assert "url: /docs/rhetorica/fr/fr-rh-001/" in fr
    assert '<a id="fr-rh-001"></a>' in fr
    assert sync_repository(tmp_path, write=False) == ([], [])


def test_sync_rejects_unknown_use_case(tmp_path: Path) -> None:
    _write_specs(tmp_path)
    path = tmp_path / "openspec/specs/requirements/functional/fr-rh-001/spec.md"
    path.write_text(FR_SPEC.replace("UC-RH-1", "UC-RH-404"), encoding="utf-8")

    _, errors = sync_repository(tmp_path, write=False)

    assert any("references missing UC-RH-404" in error for error in errors)


def test_sync_rejects_use_case_missing_from_shared_diagram(tmp_path: Path) -> None:
    _write_specs(tmp_path)
    path = tmp_path / "docs/rhetorica/uc/_index.md"
    path.write_text(UC_INDEX.replace("UC-RH-1", "UC-RH-404"), encoding="utf-8")

    _, errors = sync_repository(tmp_path, write=False)

    assert any("shared diagram does not mention UC-RH-1" in error for error in errors)


def test_sync_rejects_nfr_without_measurement_contract(tmp_path: Path) -> None:
    _write_specs(tmp_path)
    path = tmp_path / "openspec/specs/requirements/non-functional/nfr-rh-001/spec.md"
    path.write_text(NFR_SPEC.replace("**Порог:** 100%.\n", ""), encoding="utf-8")

    _, errors = sync_repository(tmp_path, write=False)

    assert any("has no **Порог:** field" in error for error in errors)


def test_sync_supports_dotted_child_requirements(tmp_path: Path) -> None:
    _write_specs(tmp_path)
    path = tmp_path / "openspec/specs/requirements/functional/fr-rh-001/spec.md"
    child = """

### Requirement: FR-RH-001.1 — PostgreSQL dialect

Система SHALL выполнять read-only SQL PostgreSQL.

**Use Cases:** UC-RH-1

#### Scenario: PostgreSQL query

- **WHEN** передан валидный PostgreSQL-запрос
- **THEN** система выполняет его
"""
    path.write_text(FR_SPEC + child, encoding="utf-8")

    _, errors = sync_repository(tmp_path, write=True)

    assert not errors
    assert '<a id="fr-rh-001-1"></a>' in path.read_text(encoding="utf-8")
