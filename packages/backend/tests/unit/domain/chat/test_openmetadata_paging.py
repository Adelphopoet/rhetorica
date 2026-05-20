"""Tests for OpenMetadata paging in metadata fetcher."""


import rhetorica_backend.domain.chat.metadata.table_loader as table_loader_module
from rhetorica_backend.contracts.admin_integrations import OpenMetadataConnectionConfig
from rhetorica_backend.domain.chat.metadata.metadata_service import MetadataFetcher


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, pages):
        self._pages = pages
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _tb):
        return False

    def get(self, url, headers=None, params=None):
        self.calls.append({"url": url, "headers": headers, "params": dict(params or {})})
        after = (params or {}).get("after")
        payload = self._pages.get(after)
        if payload is None:
            raise AssertionError(f"unexpected page token: {after}")
        return _FakeResponse(payload)


def test_fetch_openmetadata_tables_follows_after_cursor(monkeypatch) -> None:
    pages = {
        None: {
            "data": [
                {
                    "fullyQualifiedName": "svc.db.schema.other_table",
                    "columns": [],
                    "database": {"fullyQualifiedName": "svc.db"},
                    "databaseSchema": {"fullyQualifiedName": "svc.db.schema"},
                }
            ],
            "paging": {"after": "cursor-2"},
        },
        "cursor-2": {
            "data": [
                {
                    "fullyQualifiedName": "svc.db.schema.ft_main_whs_partner_date",
                    "columns": [{"name": "order_create_date"}],
                    "database": {"fullyQualifiedName": "svc.db"},
                    "databaseSchema": {"fullyQualifiedName": "svc.db.schema"},
                }
            ],
            "paging": {},
        },
    }
    fake_client = _FakeClient(pages)

    monkeypatch.setattr(
        table_loader_module.httpx,
        "Client",
        lambda **kwargs: fake_client,
    )

    fetcher = MetadataFetcher()
    connection = OpenMetadataConnectionConfig(
        base_url="https://openmetadata.local",
        table_limit=10,
    )

    tables = fetcher._fetch_openmetadata_tables(connection)

    assert [table.fqn for table in tables] == [
        "svc.db.schema.other_table",
        "svc.db.schema.ft_main_whs_partner_date",
    ]
    assert fake_client.calls[0]["params"]["limit"] == 10
    assert "after" not in fake_client.calls[0]["params"]
    assert fake_client.calls[1]["params"]["after"] == "cursor-2"
