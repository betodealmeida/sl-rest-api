from sl_db_engine_spec.metadata import d3format_from_metadata


def test_d3format_prefers_explicit_d3() -> None:
    metadata = {
        "unit": {"kind": "currency", "code": "USD"},
        "format": {"preset": "number", "precision": 0},
        "extensions": {"superset": {"d3format": "$,.2f"}},
    }

    assert d3format_from_metadata(metadata) == "$,.2f"


def test_d3format_tolerates_legacy_format_d3() -> None:
    metadata = {
        "unit": {"kind": "currency", "code": "USD"},
        "format": {"preset": "number", "precision": 0, "d3": "$,.2f"},
    }

    assert d3format_from_metadata(metadata) == "$,.2f"


def test_d3format_maps_portable_presets() -> None:
    assert d3format_from_metadata({"format": {"preset": "smart_number"}}) == (
        "SMART_NUMBER"
    )
    assert d3format_from_metadata({"format": {"preset": "number", "precision": 1}}) == (
        ",.1f"
    )
    assert d3format_from_metadata(
        {"format": {"preset": "percentage", "precision": 0}},
    ) == ".0%"
    assert d3format_from_metadata(
        {
            "unit": {"kind": "currency", "code": "USD"},
            "format": {"preset": "currency", "precision": 2},
        },
    ) == "$,.2f"


def test_d3format_omits_unknown_or_invalid_format() -> None:
    assert d3format_from_metadata({}) is None
    assert d3format_from_metadata({"format": {"preset": "duration"}}) is None
    assert d3format_from_metadata({"format": {"preset": "number", "precision": -1}}) == (
        ",.2f"
    )


def test_pseudo_catalog_engine_spec() -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    import pytest
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.engine.url import make_url

    pytest.importorskip("superset")
    from sl_db_engine_spec.engine_spec import SemanticAPIEngineSpec

    spec = SemanticAPIEngineSpec
    assert spec.supports_catalog
    assert spec.supports_dynamic_catalog
    assert spec.supports_dynamic_schema
    assert not spec.quote_table_includes_schema

    url, _ = spec.adjust_engine_params(
        make_url("semanticapi://example.test"), {}, catalog="demo", schema="metrics"
    )
    assert url.query["pseudo_catalog"] == "demo"
    assert url.query["pseudo_schema"] == "metrics"
    table = SimpleNamespace(
        table="demo.metrics.add_thumbs_demo_materialization.thumbs_cov_geo",
        schema="metrics",
        catalog="demo",
    )
    assert spec.quote_table(table, dialect()) == (
        '"demo.metrics.add_thumbs_demo_materialization.thumbs_cov_geo"'
    )

    database = Mock()
    database.get_all_catalog_names.return_value = {"prod", "demo"}
    database.get_all_schema_names.return_value = {"sales", "metrics"}
    assert spec.get_default_catalog(database) == "demo"
    assert spec.get_default_schema(database, "demo") == "metrics"
    database.get_all_schema_names.assert_called_with(catalog="demo")
