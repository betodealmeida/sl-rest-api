from __future__ import annotations

import pytest
from litestar.testing import TestClient
from semantic_api.app import create_app
from semantic_api.discovery import browse


def test_views_browse_mixed_depth_hierarchy() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/views", json={"path": []}).json() == {
            "hierarchy": {
                "next": {"id": "group", "display_name": "Group"},
                "values": ["foo"],
            },
            "views": [],
        }
        area = client.post("/views", json={"path": ["foo", "bar"]}).json()
        assert area["hierarchy"] == {
            "next": {"id": "variant", "display_name": "Variant"},
            "values": ["baz"],
        }
        assert [view["name"] for view in area["views"]] == ["marketing"]
        leaf = client.post("/views", json={"path": ["foo", "bar", "baz"]}).json()
        assert leaf["hierarchy"] == {
            "next": None,
        }
        assert [view["name"] for view in leaf["views"]] == ["sales"]


def test_omitted_path_keeps_legacy_array() -> None:
    with TestClient(create_app()) as client:
        for endpoint in ("/views", "/views/list"):
            response = client.post(endpoint, json={})
            assert response.status_code == 200
            assert {view["name"] for view in response.json()} == {"marketing", "sales"}
        assert client.post("/views/list", json={"path": []}).json()["views"] == []


def test_empty_choices_keep_the_next_level() -> None:
    hierarchy, views = browse([], [])
    assert hierarchy == {
        "next": {"id": "group", "display_name": "Group"},
        "values": [],
    }
    assert views == []


@pytest.mark.parametrize(
    ("path", "status"),
    [
        (["missing"], 404),
        (["foo", "missing"], 404),
        (["foo", "bar", None], 404),
        (["foo", "bar", "null"], 404),
        (["foo", "bar", "baz", "extra"], 400),
    ],
)
def test_invalid_discovery_path(path: list[str | None], status: int) -> None:
    with TestClient(create_app()) as client:
        response = client.post("/views", json={"path": path})
    assert response.status_code == status
    assert response.json()["detail"]


def test_views_requires_bearer_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMANTIC_LAYER_REQUIRE_AUTH", "1")
    with TestClient(create_app()) as client:
        response = client.post("/views", json={"path": []})
    assert response.status_code == 401


def test_discover_route_is_removed() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/discover", json={}).status_code == 404
