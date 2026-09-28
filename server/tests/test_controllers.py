from types import SimpleNamespace

from semantic_api.controllers import _view_payload, _view_summary_payload


def _view(**kwargs):
    values = {
        "name": "sales",
        "features": frozenset(),
        "uid": lambda: "pandas.sales",
        "get_dimensions": set,
        "get_metrics": set,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_view_payloads_include_display_name() -> None:
    view = _view(display_name="Sales overview")

    assert _view_summary_payload(view) == {
        "name": "sales",
        "display_name": "Sales overview",
        "uid": "pandas.sales",
        "features": [],
    }
    assert _view_payload(view)["display_name"] == "Sales overview"


def test_view_payloads_omit_missing_display_name() -> None:
    assert "display_name" not in _view_summary_payload(_view())
    assert "display_name" not in _view_payload(_view(display_name=""))
