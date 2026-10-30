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

"""Combined view listing controls the dynamic Semantic Layer form."""

from __future__ import annotations

import json

import pytest
import requests
from betodealmeida.semantic_layer_api.client import APIClient, APIClientError
from betodealmeida.semantic_layer_api.configuration import APIConfiguration
from betodealmeida.semantic_layer_api.layer import APISemanticLayer

NAMESPACE = {"id": "namespace", "display_name": "Namespace"}
BRANCH = {"id": "branch", "display_name": "Branch"}


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

    def browse(self, path):
        if path == []:
            return {
                "hierarchy": {
                    "next": NAMESPACE,
                    "values": ["demo.metrics"],
                },
                "views": [],
            }
        if path == ["demo.metrics"]:
            return {
                "hierarchy": {
                    "next": BRANCH,
                    "values": ["main", None],
                },
                "views": [{"name": "demo.metrics.plain"}],
            }
        if path == ["demo.metrics", "main"]:
            return {
                "hierarchy": {"next": None},
                "views": [{"name": "demo.metrics.main.download_session"}],
            }
        raise AssertionError(f"Unexpected path: {path}")

    def list_views(self, path):
        return self.browse(path)["views"]


def test_selectors_use_next_level_and_allow_direct_views(monkeypatch) -> None:
    monkeypatch.setattr("betodealmeida.semantic_layer_api.layer.APIClient", FakeClient)

    root = APISemanticLayer.get_configuration_schema(configuration())
    assert root["properties"]["hierarchy_0"]["title"] == "Namespace"
    assert root["properties"]["hierarchy_0"]["x-dynamic"] is True
    assert "hierarchy_0" in root["properties"]["hierarchy_0"]["x-dependsOn"]
    assert "hierarchy_1" not in root["properties"]

    namespace = APISemanticLayer.get_runtime_schema(
        configuration(hierarchy_0="demo.metrics"),
    )
    assert namespace["properties"]["hierarchy_1"]["title"] == "Branch"
    assert namespace["properties"]["hierarchy_1"]["enum"] == ["main", "unversioned"]
    assert namespace["properties"]["hierarchy_1"]["x-dependsOn"] == [
        "hierarchy_0",
        "hierarchy_1",
    ]
    assert "hierarchy_1" not in namespace["required"]
    assert "hierarchy_2" not in namespace["properties"]

    branch = APISemanticLayer.get_configuration_schema(
        configuration(hierarchy_0="demo.metrics", hierarchy_1="main")
    )
    assert "hierarchy_2" not in branch["properties"]


def test_selector_supports_third_level(monkeypatch) -> None:
    project = {"id": "project", "display_name": "Project"}

    class ThreeLevelClient(FakeClient):
        def browse(self, path):
            if path == ["demo.metrics", "main"]:
                return {
                    "hierarchy": {
                        "next": project,
                        "values": ["alpha"],
                    },
                    "views": [],
                }
            if path == ["demo.metrics", "main", "alpha"]:
                return {
                    "hierarchy": {"next": None},
                    "views": [{"name": "report"}],
                }
            return super().browse(path)

    monkeypatch.setattr(
        "betodealmeida.semantic_layer_api.layer.APIClient", ThreeLevelClient
    )
    schema = APISemanticLayer.get_configuration_schema(
        configuration(hierarchy_0="demo.metrics", hierarchy_1="main")
    )
    assert schema["properties"]["hierarchy_2"]["title"] == "Project"
    assert schema["properties"]["hierarchy_2"]["enum"] == ["alpha"]


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
        {"hierarchy": {"next": None, "values": ["foo"]}, "views": []},
        {
            "hierarchy": {"next": {"id": "x"}, "values": ["foo"]},
            "views": [],
        },
        {"hierarchy": {"next": NAMESPACE, "values": []}},
        {"hierarchy": {"next": NAMESPACE}, "views": []},
        {"hierarchy": {"values": []}, "views": []},
    ],
)
def test_client_rejects_invalid_hierarchy(monkeypatch, payload) -> None:
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response(payload))
    with pytest.raises(APIClientError, match="invalid hierarchy metadata"):
        APIClient(configuration()).browse([])


def test_client_uses_views_for_browsing_and_listing(monkeypatch) -> None:
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return response(FakeClient(None).browse(kwargs["json"]["path"]))

    monkeypatch.setattr(requests, "post", post)
    client = APIClient(configuration())
    assert client.browse([])["hierarchy"]["next"] == NAMESPACE
    assert client.list_views(["demo.metrics", "main"]) == [
        {"name": "demo.metrics.main.download_session"}
    ]
    assert [url for url, _ in calls] == [
        "https://dj.example.test/semantic/views",
        "https://dj.example.test/semantic/views",
    ]


def test_missing_views_route_surfaces_error(monkeypatch) -> None:
    def post(*args, **kwargs):
        result = response({"detail": "Not found"})
        result.status_code = 404
        return result

    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(APIClientError, match="Not found"):
        APIClient(configuration()).browse([])
