"""Catalog and schema discovery for dot-delimited semantic view names."""

from unittest.mock import Mock, patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import registry as dialect_registry

from sl_db_engine_spec.dialect import view_namespace


VIEWS = [
    "demo.metrics.add_thumbs_demo_materialization.thumbs_cov_geo",
    "demo.sales.orders",
    "prod.metrics.orders",
]


def response(payload):
    result = Mock(status_code=200)
    result.json.return_value = payload
    return result


def test_view_namespace_requires_catalog_schema_and_table() -> None:
    assert view_namespace(VIEWS[0]) == ("demo", "metrics")
    assert view_namespace("demo.metrics") is None
    assert view_namespace("demo..orders") is None


def test_catalog_schema_and_table_discovery() -> None:
    dialect_registry.register(
        "semanticapi", "sl_db_engine_spec.dialect", "SemanticAPIDialect"
    )

    queries = []

    def post(url, **kwargs):
        if url.endswith("/views/list"):
            return response([{"name": name} for name in VIEWS])
        if url.endswith(
            "/views/demo.metrics.add_thumbs_demo_materialization.thumbs_cov_geo/query"
        ):
            queries.append(kwargs["json"]["query"])
            return response({"results": {"rows": [{"region": "West", "revenue": 42.0}]}})
        if url.endswith(
            "/views/demo.metrics.add_thumbs_demo_materialization.thumbs_cov_geo"
        ):
            return response(
                {
                    "dimensions": [
                        {"name": "region", "id": "region-id", "type": "utf8"}
                    ],
                    "metrics": [
                        {"name": "revenue", "id": "revenue-id", "type": "floating"}
                    ],
                }
            )
        raise AssertionError(f"Unexpected request: {url}")

    with patch("sl_db_engine_spec.dialect.requests.post", side_effect=post):
        engine = create_engine("semanticapi://example.test")
        inspector = inspect(engine)
        assert inspector.dialect.get_catalog_names() == ["demo", "prod"]
        assert inspector.get_schema_names() == ["metrics", "sales"]
        assert inspector.get_table_names(schema="metrics") == [VIEWS[0], VIEWS[2]]
        engine.dispose()

        scoped = create_engine(
            "semanticapi://example.test?pseudo_catalog=demo&pseudo_schema=metrics"
        )
        inspector = inspect(scoped)
        assert inspector.get_schema_names() == ["metrics", "sales"]
        assert inspector.get_table_names(schema="metrics") == [VIEWS[0]]
        assert inspector.get_table_names(schema="sales") == []
        assert inspector.has_table(VIEWS[0], schema="metrics")
        assert not inspector.has_table(VIEWS[2], schema="metrics")
        assert not inspector.has_table(VIEWS[1], schema="sales")
        assert scoped.dialect._adapter_cls.table_names == {VIEWS[0]}
        columns = inspector.get_columns(VIEWS[0], "metrics")
        assert [column["name"] for column in columns] == [
            "region",
            "revenue",
        ]

        other = create_engine("semanticapi://example.test?pseudo_catalog=prod")
        other_inspector = inspect(other)
        assert other_inspector.get_table_names(schema="metrics") == [VIEWS[2]]
        assert not other_inspector.has_table(VIEWS[0], schema="metrics")
        assert inspector.has_table(VIEWS[0], schema="metrics")

        with scoped.connect() as connection:
            rows = connection.execute(
                text(f'SELECT region, revenue FROM "{VIEWS[0]}"')
            ).fetchall()
        assert rows == [("West", 42.0)]
        assert queries[-1]["dimensions"] == ["region-id"]
        assert queries[-1]["metrics"] == ["revenue-id"]
        other.dispose()
        scoped.dispose()
