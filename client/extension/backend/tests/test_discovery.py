# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Discovery metadata controls the dynamic Semantic Layer form."""

from __future__ import annotations

import json

import pytest
import requests

from betodealmeida.semantic_layer_api.client import APIClient, APIClientError
from betodealmeida.semantic_layer_api.configuration import APIConfiguration
from betodealmeida.semantic_layer_api.layer import APISemanticLayer


def configuration(**overrides) -> APIConfiguration:
    return APIConfiguration.model_validate(
        {
            "api_url": "https://dj.example.test/semantic",
            "auth_type": "access_token",
            "access_token": "test-token",
            **overrides,
        }
    )


def response(payload: object) -> requests.Response:
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps(payload).encode()
    return result


class FakeClient:
    def __init__(self, configuration, additional_configuration=None) -> None:
        self.additional_configuration = additional_configuration or {}

    def discover(self, path):
        if not path:
            return {
                "level": {"id": "namespace", "display_name": "Namespace"},
                "values": ["demo.metrics"],
                "next_level": {"id": "branch", "display_name": "Branch"},
            }
        if path == ["demo.metrics"]:
            return {
                "level": {"id": "branch", "display_name": "Branch"},
                "values": ["main", None],
            }
        raise AssertionError(f"Unexpected discovery path: {path}")

    def list_views(self, path):
        assert path == ["demo.metrics", "main"]
        return [{"name": "demo.metrics.main.download_session"}]


def test_next_level_labels_pending_field_and_terminal_level_stops(monkeypatch) -> None:
    monkeypatch.setattr("betodealmeida.semantic_layer_api.layer.APIClient", FakeClient)

    root = APISemanticLayer.get_configuration_schema(configuration())
    assert root["properties"]["hierarchy_0"]["title"] == "Namespace"
    assert root["properties"]["hierarchy_1"]["title"] == "Branch"
    assert root["properties"]["hierarchy_1"]["readOnly"] is True

    branch = APISemanticLayer.get_configuration_schema(
        configuration(hierarchy_0="demo.metrics")
    )
    assert branch["properties"]["hierarchy_1"]["enum"] == [
        "main",
        "unversioned",
    ]
    assert "hierarchy_2" not in branch["properties"]


def test_discovery_supports_a_third_level(monkeypatch) -> None:
    class ThreeLevelClient(FakeClient):
        def discover(self, path):
            if path == ["demo.metrics"]:
                return {
                    "level": {"id": "branch", "display_name": "Branch"},
                    "values": ["main"],
                    "next_level": {"id": "project", "display_name": "Project"},
                }
            if path == ["demo.metrics", "main"]:
                return {
                    "level": {"id": "project", "display_name": "Project"},
                    "values": ["alpha"],
                }
            return super().discover(path)

    monkeypatch.setattr(
        "betodealmeida.semantic_layer_api.layer.APIClient", ThreeLevelClient
    )
    branch = APISemanticLayer.get_configuration_schema(
        configuration(hierarchy_0="demo.metrics")
    )
    assert branch["properties"]["hierarchy_2"]["title"] == "Project"

    project = APISemanticLayer.get_configuration_schema(
        configuration(hierarchy_0="demo.metrics", hierarchy_1="main")
    )
    assert project["properties"]["hierarchy_2"]["enum"] == ["alpha"]
    assert "hierarchy_3" not in project["properties"]


def test_fixed_path_rejects_outside_view_at_use_time(monkeypatch) -> None:
    monkeypatch.setattr("betodealmeida.semantic_layer_api.layer.APIClient", FakeClient)
    layer = APISemanticLayer(
        configuration(hierarchy_0="demo.metrics", hierarchy_1="main")
    )

    with pytest.raises(ValueError, match="conflicts"):
        layer.get_semantic_view(
            "demo.metrics.other.report",
            {"hierarchy_0": "demo.metrics", "hierarchy_1": "other"},
        )
    with pytest.raises(ValueError, match="outside"):
        layer.get_semantic_view("demo.metrics.other.report", {})


@pytest.mark.parametrize(
    "payload",
    [
        {
            "level": {"id": "namespace", "display_name": "Namespace"},
            "values": ["demo.metrics"],
            "has_next": True,
        },
        {
            "level": {"id": "namespace", "display_name": "Namespace"},
            "values": ["demo.metrics"],
            "next_level": None,
        },
    ],
)
def test_client_rejects_legacy_or_null_next_level(monkeypatch, payload) -> None:
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response(payload))
    with pytest.raises(APIClientError, match="invalid discovery metadata"):
        APIClient(configuration()).discover([])


def test_client_accepts_nonterminal_and_terminal_responses(monkeypatch) -> None:
    payloads = [FakeClient(None).discover([]), FakeClient(None).discover(["demo.metrics"])]
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: response(payloads.pop(0)),
    )
    client = APIClient(configuration())
    assert client.discover([])["next_level"]["display_name"] == "Branch"
    assert "next_level" not in client.discover(["demo.metrics"])


def test_missing_discovery_route_does_not_fall_back(monkeypatch) -> None:
    paths = []

    def post(url, **kwargs):
        paths.append(url)
        result = response({"detail": "Not found"})
        result.status_code = 404
        return result

    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(APIClientError, match="Not found"):
        APIClient(configuration()).discover([])
    assert paths == ["https://dj.example.test/semantic/views/discover/"]

    schema = APISemanticLayer.get_configuration_schema(configuration())
    assert schema["properties"]["hierarchy_0"]["readOnly"] is True
    assert paths == [
        "https://dj.example.test/semantic/views/discover/",
        "https://dj.example.test/semantic/views/discover/",
    ]


def test_view_listing_always_uses_filtered_endpoint(monkeypatch) -> None:
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return response([])

    monkeypatch.setattr(requests, "post", post)
    client = APIClient(configuration())
    assert client.list_views([]) == []
    assert client.list_views(["demo.metrics", None]) == []
    assert calls == [
        (
            "https://dj.example.test/semantic/views/",
            {"runtime_configuration": {}, "path": []},
        ),
        (
            "https://dj.example.test/semantic/views/",
            {"runtime_configuration": {}, "path": ["demo.metrics", None]},
        ),
    ]
