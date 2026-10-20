from __future__ import annotations

import pytest
from litestar.testing import TestClient

from semantic_api.app import create_app


def test_discover_mixed_depth_hierarchy() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/discover", json={}).json() == {
            "level": {"id": "group", "display_name": "Group"},
            "values": ["foo"],
            "next_level": {"id": "area", "display_name": "Area"},
        }
        assert client.post("/discover", json={"path": ["foo"]}).json() == {
            "level": {"id": "area", "display_name": "Area"},
            "values": ["bar"],
            "next_level": {"id": "variant", "display_name": "Variant"},
        }
        assert client.post("/discover", json={"path": ["foo", "bar"]}).json() == {
            "level": {"id": "variant", "display_name": "Variant"},
            "values": ["baz", None],
        }


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (None, {"marketing", "sales"}),
        ([], {"marketing", "sales"}),
        (["foo"], {"marketing", "sales"}),
        (["foo", "bar"], {"marketing", "sales"}),
        (["foo", "bar", None], {"marketing"}),
        (["foo", "bar", "baz"], {"sales"}),
    ],
)
def test_list_views_filters_by_hierarchy(
    path: list[str | None] | None, expected: set[str]
) -> None:
    body = {} if path is None else {"path": path}
    with TestClient(create_app()) as client:
        for endpoint in ("/views/", "/views/list"):
            response = client.post(endpoint, json=body)
            assert response.status_code == 200
            assert {view["name"] for view in response.json()} == expected


@pytest.mark.parametrize(
    ("endpoint", "path", "status"),
    [
        ("/discover", ["missing"], 404),
        ("/discover", ["foo", "bar", "baz"], 400),
        ("/views/", ["foo", "missing"], 404),
        ("/views/", ["foo", "bar", "missing"], 404),
        ("/views/", ["foo", "bar", "null"], 404),
        ("/views/", ["foo", "bar", "baz", "extra"], 400),
    ],
)
def test_invalid_discovery_path(
    endpoint: str, path: list[str | None], status: int
) -> None:
    with TestClient(create_app()) as client:
        response = client.post(endpoint, json={"path": path})
    assert response.status_code == status
    assert response.json()["detail"]


def test_discover_requires_bearer_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMANTIC_LAYER_REQUIRE_AUTH", "1")
    with TestClient(create_app()) as client:
        response = client.post("/discover", json={})
    assert response.status_code == 401
