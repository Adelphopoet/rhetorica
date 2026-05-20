"""Tests for OpenMetadata catalog query shaping policy."""

import json

from rhetorica_backend.domain.chat.metadata.catalog_query_policy import (
    MetadataCatalogQueryPolicy,
)


def test_build_search_request_defaults_single_keyword_to_table_lookup() -> None:
    request = MetadataCatalogQueryPolicy.build_search_request(
        entity_type=None,
        query="price",
    )

    assert request.entity_type == "table"
    assert request.query_text == "*price*"
    payload = json.loads(request.query_filter)
    assert {"term": {"entityType": "table"}} in payload["bool"]["must"]


def test_build_search_request_uses_filter_only_for_multi_token_discovery() -> None:
    request = MetadataCatalogQueryPolicy.build_search_request(
        entity_type=None,
        query="цена товара price product",
    )

    assert request.entity_type is None
    assert request.query_text == "*"
    payload = json.loads(request.query_filter)
    must = payload["bool"]["must"]
    entity_clause = must[0]["bool"]
    assert {"term": {"entityType": "table"}} in entity_clause["should"]
    assert {"term": {"entityType": "glossaryTerm"}} in entity_clause["should"]
    assert {"term": {"entityType": "metric"}} in entity_clause["should"]
    token_clause = must[-1]["bool"]
    assert token_clause["minimum_should_match"] == 1
    assert {"match": {"name": "price"}} in token_clause["should"]
    assert {"match": {"columns.name": "product"}} in token_clause["should"]


def test_build_search_request_preserves_explicit_search_syntax() -> None:
    request = MetadataCatalogQueryPolicy.build_search_request(
        entity_type="table",
        query="tags.tagFQN:user.email AND owner.displayName.keyword:pricing",
    )

    assert request.query_text == "tags.tagFQN:user.email AND owner.displayName.keyword:pricing"
    payload = json.loads(request.query_filter)
    assert payload["bool"]["must"] == [{"term": {"entityType": "table"}}]
