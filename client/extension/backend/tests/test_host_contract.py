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

"""Exercise the provider entry points with the current host SDK contract."""

from datetime import date
from time import monotonic
from unittest.mock import Mock

import pytest
from betodealmeida.semantic_layer_api.client import APIClient, APIClientError
from betodealmeida.semantic_layer_api.layer import APISemanticLayer
from superset_core.semantic_layers.config import SemanticCredentialReadiness
from superset_core.semantic_layers.context import SemanticCredentials
from superset_core.semantic_layers.layer import SemanticLayerAuthenticationRequiredError
from superset_core.semantic_layers.transport import (
    ProviderRequestError,
    ProviderRequestFailure,
    ProviderResponse,
)
from test_discovery import configuration, response


@pytest.mark.parametrize("runtime", [False, True])
@pytest.mark.parametrize("auth_type", ["access_token", "oauth2"])
def test_unresolved_schema_never_looks_up_credentials_or_uses_network(
    monkeypatch, transport, runtime, auth_type
):
    lookup = Mock(side_effect=AssertionError("Unexpected credential lookup"))
    monkeypatch.setattr(
        "betodealmeida.semantic_layer_api.layer.has_usable_credentials", lookup
    )
    config = configuration(
        auth_type=auth_type,
        client_id="client",
        client_secret="secret",
        hierarchy_0="sales",
    )
    kwargs = {
        "transport": transport,
        "readiness": SemanticCredentialReadiness.UNRESOLVED,
    }
    if runtime:
        schema = APISemanticLayer.get_runtime_schema(config, **kwargs)
    else:
        schema = APISemanticLayer.get_configuration_schema(config, **kwargs)
    assert schema["properties"]["hierarchy_0"]["default"] == "sales"
    assert schema["properties"]["hierarchy_0"]["readOnly"] is True
    lookup.assert_not_called()
    transport.request.assert_not_called()


@pytest.mark.parametrize("readiness", list(SemanticCredentialReadiness))
def test_schema_without_configuration_is_static(monkeypatch, transport, readiness):
    lookup = Mock(side_effect=AssertionError("Unexpected credential lookup"))
    monkeypatch.setattr(
        "betodealmeida.semantic_layer_api.layer.has_usable_credentials", lookup
    )
    schema = APISemanticLayer.get_configuration_schema(
        transport=transport, readiness=readiness
    )
    assert schema["properties"]["client_secret"]["writeOnly"] is True
    lookup.assert_not_called()
    transport.request.assert_not_called()


def test_resolved_schema_uses_host_transport(transport):
    transport.request.return_value = response(
        {
            "hierarchy": {
                "next": {"id": "namespace", "display_name": "Namespace"},
                "values": ["sales"],
            },
            "views": [{"name": "@explore"}],
        }
    )
    schema = APISemanticLayer.get_configuration_schema(
        configuration(),
        transport=transport,
        readiness=SemanticCredentialReadiness.RESOLVED,
    )
    assert schema["properties"]["hierarchy_0"]["enum"] == ["sales"]
    assert transport.request.call_args.args == (
        "Semantic Layer API",
        "POST",
        "https://dj.example.test/semantic/views",
    )


def test_binding_reaches_saved_and_discovered_views(binding, transport):
    layer = APISemanticLayer.from_configuration(
        configuration().model_dump(mode="json") | {"access_token": "test-token"},
        **binding,
    )
    assert layer.context is binding["context"]
    assert layer.cache_token == binding["cache_token"]
    transport.request.return_value = response(
        {"hierarchy": {"next": None}, "views": [{"name": "@explore:sales:main"}]}
    )
    views = layer.get_semantic_views({})
    assert {view.name for view in views} == {"@explore:sales:main"}
    transport.request.return_value = response(
        {"name": "@explore:sales:main", "metrics": [], "dimensions": []}
    )
    view = layer.get_semantic_view("@explore:sales:main", {})
    assert view.get_metrics() == set()
    assert transport.request.call_args.args[2].endswith("/views/@explore:sales:main")


def test_nonconnection_credentials_do_not_fall_back(binding):
    with pytest.raises(ValueError, match="connection credentials"):
        APISemanticLayer.from_configuration(
            {}, credentials=SemanticCredentials(), **binding
        )


def test_request_is_bounded_and_serializes_values(transport):
    transport.request.return_value = response({})
    before = monotonic()
    APIClient(configuration(request_timeout=17), transport=transport).query(
        "sales", {"day": date(2026, 11, 12)}
    )
    kwargs = transport.request.call_args.kwargs
    assert before + 17 <= kwargs["budget"].deadline <= monotonic() + 17
    assert 0 < kwargs["budget"].max_bytes <= 64 * 1024 * 1024
    assert kwargs["headers"] == {"Authorization": "Bearer test-token"}
    assert kwargs["json"]["query"]["day"] == "2026-11-12"


@pytest.mark.parametrize("status", [401, 302])
@pytest.mark.parametrize("auth_type", ["oauth2", "access_token"])
def test_authentication_rejections(monkeypatch, transport, status, auth_type):
    monkeypatch.setattr(
        "betodealmeida.semantic_layer_api.client.get_access_token",
        lambda config: "personal-token",
    )
    discard = Mock()
    monkeypatch.setattr(
        "betodealmeida.semantic_layer_api.client.discard_token", discard
    )
    transport.request.side_effect = ProviderRequestError(
        ProviderRequestFailure.HTTP_STATUS, status=status
    )
    client = APIClient(
        configuration(auth_type=auth_type, client_id="client", client_secret="secret"),
        transport=transport,
    )
    error = (
        SemanticLayerAuthenticationRequiredError
        if auth_type == "oauth2"
        else APIClientError
    )
    with pytest.raises(error):
        client.browse([])
    assert discard.call_count == (1 if auth_type == "oauth2" else 0)


@pytest.mark.parametrize(
    "failure",
    [
        ProviderRequestFailure.TIMEOUT,
        ProviderRequestFailure.TOO_LARGE,
        ProviderRequestFailure.REFUSED,
    ],
)
def test_transport_failure_becomes_client_error(transport, failure):
    transport.request.side_effect = ProviderRequestError(
        failure, provider="Semantic Layer API"
    )
    with pytest.raises(APIClientError):
        APIClient(configuration(), transport=transport).browse([])


def test_invalid_json_response(transport):
    transport.request.return_value = ProviderResponse(
        200, {}, b"not json", "https://dj.example.test"
    )
    with pytest.raises(APIClientError, match="non-JSON"):
        APIClient(configuration(), transport=transport).browse([])
