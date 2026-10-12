from __future__ import annotations

import json
from pathlib import Path

import pytest
from rhetorica_backend.domain.coding.trino_sql_policy import StrictTrinoReadOnlyPolicy


@pytest.fixture(scope="module")
def corpus() -> dict[str, list[dict[str, str]]]:
    path = (
        Path(__file__).parents[2]
        / "fixtures"
        / "coding"
        / "dbt_trino_sql.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_real_dbt_trino_compatibility_corpus_is_allowed(corpus) -> None:
    policy = StrictTrinoReadOnlyPolicy()

    decisions = [(item["command"], policy.evaluate(item["sql"])) for item in corpus["allowed"]]

    assert all(decision.allowed for _command, decision in decisions), decisions
    assert {command for command, _decision in decisions} == {
        "parse",
        "ls",
        "compile",
        "show",
        "data_test",
    }


def test_malicious_dbt_sql_corpus_is_denied(corpus) -> None:
    policy = StrictTrinoReadOnlyPolicy()

    decisions = [(item["source"], policy.evaluate(item["sql"])) for item in corpus["denied"]]

    assert all(not decision.allowed for _source, decision in decisions), decisions


def test_policy_preserves_sql_without_adding_limit() -> None:
    sql = "SELECT order_id FROM iceberg.analytics.orders"

    decision = StrictTrinoReadOnlyPolicy().evaluate(sql)

    assert decision.allowed is True
    assert decision.sql == sql


@pytest.mark.parametrize(
    "sql",
    [
        "SHOW TABLES FROM iceberg.analytics trailing",
        "EXPLAIN (TYPE VALIDATE) DELETE FROM iceberg.analytics.orders",
        "SELECT 1; SELECT 2",
    ],
)
def test_policy_has_no_prefix_fallback(sql: str) -> None:
    assert StrictTrinoReadOnlyPolicy().evaluate(sql).allowed is False
