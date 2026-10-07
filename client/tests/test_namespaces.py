"""Catalog and schema discovery for dot-delimited semantic view names."""

from unittest.mock import Mock, patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import registry as dialect_registry
from sqlalchemy.engine.url import URL

from sl_db_engine_spec.dialect import encode_namespace_label, view_namespace


VIEWS = [
    "demo.metrics.add_thumbs_demo_materialization.thumbs_cov_geo",
    "demo.sales.orders",
    "prod.metrics.orders",
]


def response(payload):
    result = Mock(status_code=200)
    result.json.return_value = payload
    return result


def not_found():
    result = Mock(status_code=404)
    return result


def test_view_namespace_requires_catalog_schema_and_table() -> None:
    assert view_namespace(VIEWS[0]) == ("demo", "metrics")
    assert view_namespace("demo.metrics") is None
    assert view_namespace("demo..orders") is None


def test_namespace_labels_escape_dots_and_percent_signs() -> None:
    assert encode_namespace_label("demo.metrics") == "demo%2Emetrics"
    assert encode_namespace_label("demo%2Emetrics") == "demo%252Emetrics"
    assert encode_namespace_label("feature/foo.bar") == "feature%2Ffoo%2Ebar"


def test_generic_discovery_and_filtered_view_listing() -> None:
    dialect_registry.register(
        "semanticapi", "sl_db_engine_spec.dialect", "SemanticAPIDialect"
    )
    view_name = "demo.metrics.main.download_session"
    requests = []

    def post(url, **kwargs):
        body = kwargs["json"]
        requests.append((url, body))
        if url.endswith("/views/discover/"):
            if body["path"] == []:
                return response(
                    {
                        "level": {"id": "namespace", "display_name": "Namespace"},
                        "values": ["demo.metrics", "shared"],
                        "has_next": True,
                    }
                )
            if body["path"] == ["demo.metrics"]:
                return response(
                    {
                        "level": {"id": "branch", "display_name": "Branch"},
                        "values": ["main", "feature/foo.bar"],
                        "has_next": False,
                    }
                )
        if url.endswith("/views/"):
            if body.get("path") == ["demo.metrics", "main"]:
                return response([{"name": view_name}])
            return response([])
        if url.endswith("/views/list"):
            return response([])
        raise AssertionError(f"Unexpected request: {url}")

    with patch("sl_db_engine_spec.dialect.requests.post", side_effect=post):
        engine = create_engine("semanticapi://example.test")
        assert engine.dialect.get_catalog_names() == ["demo%2Emetrics", "shared"]
        engine.dispose()

        catalog_url = URL.create(
            "semanticapi",
            host="example.test",
            query={"pseudo_catalog": "demo%2Emetrics"},
        )
        catalog_engine = create_engine(catalog_url)
        assert inspect(catalog_engine).get_schema_names() == [
            "main", "feature%2Ffoo%2Ebar"
        ]
        catalog_engine.dispose()

        scoped_url = URL.create(
            "semanticapi",
            host="example.test",
            query={"pseudo_catalog": "demo%2Emetrics", "pseudo_schema": "main"},
        )
        scoped = create_engine(scoped_url)
        assert inspect(scoped).get_table_names(schema="main") == [view_name]
        assert scoped.dialect._adapter_cls.table_names == {view_name}
        scoped.dispose()

    assert any(
        body.get("path") == ["demo.metrics", "main"]
        for url, body in requests
        if url.endswith("/views/")
    )


def test_explicit_namespace_and_branch_control_discovery() -> None:
    dialect_registry.register(
        "semanticapi", "sl_db_engine_spec.dialect", "SemanticAPIDialect"
    )
    views = [
        {
            "name": "demo.metrics.main.download_session",
            "namespace": "demo.metrics",
            "branch": "main",
        },
        {
            "name": "shared.main.thumb_report",
            "namespace": "shared",
            "branch": "main",
        },
        {"name": "legacy.plain.cube"},
    ]
    queries = []

    def post(url, **kwargs):
        if url.endswith("/views/discover/"):
            return not_found()
        if url.endswith("/views/list"):
            return response(views)
        if url.endswith("/views/demo.metrics.main.download_session/query"):
            queries.append(kwargs["json"]["query"])
            return response({"results": {"rows": [{"region": "West"}]}})
        if url.endswith("/views/demo.metrics.main.download_session"):
            return response(
                {
                    "dimensions": [
                        {"name": "region", "id": "region-id", "type": "utf8"}
                    ],
                    "metrics": [],
                }
            )
        raise AssertionError(f"Unexpected request: {url}")

    with patch("sl_db_engine_spec.dialect.requests.post", side_effect=post):
        engine = create_engine("semanticapi://example.test")
        inspector = inspect(engine)
        assert inspector.dialect.get_catalog_names() == ["demo%2Emetrics", "shared"]
        assert inspector.get_schema_names() == ["main"]
        assert inspector.get_table_names(schema="main") == [
            "demo.metrics.main.download_session",
            "shared.main.thumb_report",
        ]
        engine.dispose()

        scoped_url = URL.create(
            "semanticapi",
            host="example.test",
            query={"pseudo_catalog": "demo%2Emetrics", "pseudo_schema": "main"},
        )
        scoped = create_engine(scoped_url)
        inspector = inspect(scoped)
        assert inspector.get_schema_names() == ["main"]
        assert inspector.get_table_names(schema="main") == [
            "demo.metrics.main.download_session"
        ]
        assert inspector.has_table("demo.metrics.main.download_session", "main")
        assert not inspector.has_table("shared.main.thumb_report", "main")
        assert scoped.dialect._adapter_cls.table_names == {
            "demo.metrics.main.download_session"
        }
        with scoped.connect() as connection:
            rows = connection.execute(
                text('SELECT region FROM "demo.metrics.main.download_session"')
            ).fetchall()
        assert rows == [("West",)]
        assert queries[-1]["dimensions"] == ["region-id"]
        scoped.dispose()


def test_catalog_schema_and_table_discovery() -> None:
    dialect_registry.register(
        "semanticapi", "sl_db_engine_spec.dialect", "SemanticAPIDialect"
    )

    queries = []

    def post(url, **kwargs):
        if url.endswith("/views/discover/"):
            return not_found()
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
        assert inspector.get_view_names(schema="metrics") == []
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
