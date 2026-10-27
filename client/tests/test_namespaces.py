"""Generic discovery of catalog and schema values for semantic views."""

from unittest.mock import Mock, patch

import pytest
import requests
from sl_db_engine_spec.dialect import (
    SemanticAPIDialect,
    decode_schema_label,
    encode_namespace_label,
    encode_schema_label,
)
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import registry as dialect_registry
from sqlalchemy.engine.url import URL


def response(payload):
    result = Mock(status_code=200)
    result.json.return_value = payload
    return result


def test_namespace_labels_escape_dots_and_percent_signs() -> None:
    assert encode_namespace_label("demo.metrics") == "demo%2Emetrics"
    assert encode_namespace_label("demo%2Emetrics") == "demo%252Emetrics"
    assert encode_namespace_label("feature/foo.bar") == "feature%2Ffoo%2Ebar"
    assert encode_schema_label(None) == "unversioned"
    assert encode_schema_label("unversioned") == "~unversioned"
    assert encode_schema_label("~scratch") == "~~scratch"
    assert decode_schema_label("unversioned") is None
    assert decode_schema_label("~unversioned") == "unversioned"
    assert decode_schema_label("~~scratch") == "~scratch"


def test_discovery_uses_position_and_filtered_listing_for_sql() -> None:
    dialect_registry.register(
        "semanticapi", "sl_db_engine_spec.dialect", "SemanticAPIDialect"
    )
    view_name = "demo.metrics.main.download_session"
    plain_view = "system.dj.superset_unversioned_demo"
    literal_view = "demo.metrics.literal.report"
    requests_made = []
    queries = []

    def post(url, **kwargs):
        body = kwargs["json"]
        requests_made.append((url, body))
        if url.endswith("/views"):
            if "path" not in body:
                return response([{"name": view_name}, {"name": plain_view}])
            path = body["path"]
            if not path:
                return response(
                    {
                        "hierarchy": {
                            "current": None,
                            "next": {"id": "group", "display_name": "Group"},
                            "values": ["demo.metrics", "shared", "system.dj"],
                        },
                        "views": [],
                    }
                )
            if len(path) == 1:
                values = (
                    ["main", "feature/foo.bar", "unversioned"]
                    if path == ["demo.metrics"]
                    else [None]
                )
                return response(
                    {
                        "hierarchy": {
                            "current": {"id": "group", "display_name": "Group"},
                            "next": {"id": "version", "display_name": "Version"},
                            "values": values,
                        },
                        "views": [],
                    }
                )
            views = []
            if path == ["demo.metrics", "main"]:
                # DJ-specific fields must not override the selected path.
                views = [{"name": view_name, "namespace": "wrong", "branch": "wrong"}]
            elif path == ["demo.metrics", "unversioned"]:
                views = [{"name": literal_view}]
            elif path == ["system.dj", None]:
                views = [{"name": plain_view}]
            return response(
                {
                    "hierarchy": {
                        "current": {"id": "version", "display_name": "Version"},
                        "next": None,
                        "values": [],
                    },
                    "views": views,
                }
            )
        if url.endswith(f"/views/{view_name}/query"):
            queries.append(body["query"])
            return response({"results": {"rows": [{"region": "West"}]}})
        if url.endswith(f"/views/{view_name}"):
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
        assert engine.dialect.get_catalog_names() == [
            "demo%2Emetrics",
            "shared",
            "system%2Edj",
        ]
        assert inspect(engine).get_schema_names() == []
        engine.dispose()

        catalog_engine = create_engine(
            URL.create(
                "semanticapi",
                host="example.test",
                query={"pseudo_catalog": "demo%2Emetrics"},
            )
        )
        assert inspect(catalog_engine).get_schema_names() == [
            "main",
            "feature%2Ffoo%2Ebar",
            "~unversioned",
        ]
        assert inspect(catalog_engine).get_table_names(schema="main") == [view_name]
        assert inspect(catalog_engine).has_table(view_name, schema="main")
        assert not inspect(catalog_engine).has_table(view_name, schema="~unversioned")
        catalog_engine.dispose()

        scoped = create_engine(
            URL.create(
                "semanticapi",
                host="example.test",
                query={"pseudo_catalog": "demo%2Emetrics", "pseudo_schema": "main"},
            )
        )
        assert inspect(scoped).get_table_names(schema="main") == [view_name]
        assert inspect(scoped).has_table(view_name, schema="main")
        assert inspect(scoped).get_table_names(schema="~unversioned") == []
        assert scoped.dialect._adapter_cls.table_names == {view_name}
        with scoped.connect() as connection:
            rows = connection.execute(
                text(f'SELECT region FROM "{view_name}"')
            ).fetchall()
        assert rows == [("West",)]
        assert queries[-1]["dimensions"] == ["region-id"]
        scoped.dispose()

        plain_engine = create_engine(
            URL.create(
                "semanticapi",
                host="example.test",
                query={"pseudo_catalog": "system%2Edj", "pseudo_schema": "unversioned"},
            )
        )
        assert inspect(plain_engine).get_schema_names() == ["unversioned"]
        assert inspect(plain_engine).get_table_names(schema="unversioned") == [
            plain_view
        ]
        plain_engine.dispose()

        literal_engine = create_engine(
            URL.create(
                "semanticapi",
                host="example.test",
                query={
                    "pseudo_catalog": "demo%2Emetrics",
                    "pseudo_schema": "~unversioned",
                },
            )
        )
        assert inspect(literal_engine).get_table_names(schema="~unversioned") == [
            literal_view
        ]
        literal_engine.dispose()

    assert all(not url.endswith("/views/list") for url, _ in requests_made)
    assert any(
        body.get("path") == ["demo.metrics", "main"]
        for url, body in requests_made
        if url.endswith("/views")
    )
    assert any(
        body.get("path") == ["system.dj", None]
        for url, body in requests_made
        if url.endswith("/views")
    )


@pytest.mark.parametrize(
    "path,payload",
    [
        (
            [],
            {
                "hierarchy": {
                    "current": None,
                    "next": None,
                    "values": ["demo.metrics"],
                },
                "views": [],
            },
        ),
        (
            ["demo.metrics"],
            {
                "hierarchy": {
                    "current": {"id": "group", "display_name": "Group"},
                    "next": None,
                    "values": ["main"],
                },
                "views": [],
            },
        ),
    ],
)
def test_browse_requires_two_levels(path, payload) -> None:
    dialect = SemanticAPIDialect()
    dialect._configuration = {}
    with patch.object(dialect, "_post", return_value=response(payload)):
        with pytest.raises(ValueError, match="exactly two levels"):
            dialect._browse(path)


def test_missing_views_endpoint_is_an_error() -> None:
    dialect = SemanticAPIDialect()
    dialect._configuration = {}
    missing = Mock(status_code=404)
    missing.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
    with patch.object(dialect, "_post", return_value=missing):
        with pytest.raises(requests.HTTPError, match="404"):
            dialect._browse([])
