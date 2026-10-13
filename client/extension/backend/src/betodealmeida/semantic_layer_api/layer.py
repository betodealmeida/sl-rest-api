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

"""Superset semantic-layer contribution for the Semantic Layer REST API."""

from __future__ import annotations

from typing import Any

from superset.constants import PASSWORD_MASK

from superset_core.semantic_layers.decorators import semantic_layer
from superset_core.semantic_layers.layer import (
    SemanticLayer,
    SemanticLayerAction,
    SemanticLayerActionResult,
    SemanticLayerAuthenticationRequiredError,
)

from .client import APIClient, APIClientError
from .configuration import (
    APIConfiguration,
    HIERARCHY_FIELD_PREFIX,
    encode_hierarchy_value,
    hierarchy_path_from_fields,
)
from .oauth import ACTION_ID, has_usable_credentials, start_authorization
from .view import APISemanticView


def _hierarchy_field(index: int) -> str:
    return f"{HIERARCHY_FIELD_PREFIX}{index}"


def _labels(values: list[str | None]) -> list[str]:
    return [
        "unversioned"
        if value is None
        else "unversioned (named value)"
        if value == "unversioned" and None in values
        else value
        for value in values
    ]


def _can_discover(configuration: APIConfiguration) -> bool:
    if not configuration.api_url:
        return False
    if configuration.auth_type == "access_token":
        token = configuration.access_token
        return bool(token and token.get_secret_value() not in ("", PASSWORD_MASK))
    return bool(
        configuration.client_id
        and configuration.client_secret
        and configuration.client_secret.get_secret_value()
        and has_usable_credentials(configuration)
    )


def _placeholder(
    index: int, dependencies: list[str], title: str | None = None
) -> dict[str, Any]:
    return {
        "type": "string",
        "title": title or ("More choices" if index else "Discovery location"),
        "readOnly": True,
        "x-dynamic": True,
        "x-dependsOn": dependencies,
    }


def _add_unavailable_fields(
    schema: dict[str, Any],
    selected_path: list[str | None],
    dependencies: list[str],
    description: str,
) -> None:
    """Keep saved selections visible while discovery credentials are unavailable."""
    properties = schema["properties"]
    order = schema["x-propertyOrder"]
    for index, value in enumerate(selected_path):
        field = _hierarchy_field(index)
        properties[field] = _placeholder(index, dependencies)
        properties[field]["description"] = description
        properties[field].update(
            {
                "title": f"Hierarchy level {index + 1}",
                "enum": [encode_hierarchy_value(value)],
                "x-enumNames": _labels([value]),
                "default": encode_hierarchy_value(value),
            }
        )
        order.append(field)
    if not selected_path:
        field = _hierarchy_field(0)
        properties[field] = _placeholder(0, dependencies)
        properties[field]["description"] = description
        order.append(field)


def _add_discovery_fields(
    schema: dict[str, Any],
    client: APIClient,
    selected_path: list[str | None],
    locked_count: int,
    first_dependencies: list[str],
    *,
    require_selection: bool,
) -> None:
    """Add each known level and a placeholder for the next unknown level."""
    properties = schema["properties"]
    order = schema["x-propertyOrder"]
    required = schema.setdefault("required", [])
    path: list[str | None] = []
    while True:
        discovery = client.discover(path)
        index = len(path)
        field = _hierarchy_field(index)
        values = discovery["values"]
        dependencies = (
            first_dependencies if index == 0 else [_hierarchy_field(index - 1)]
        )
        properties[field] = {
            "type": "string",
            "title": discovery["level"]["display_name"],
            "enum": [encode_hierarchy_value(value) for value in values],
            "x-enumNames": _labels(values),
        }
        if dependencies:
            properties[field]["x-dynamic"] = True
            properties[field]["x-dependsOn"] = dependencies
        if index < locked_count:
            properties[field]["readOnly"] = True
            properties[field]["default"] = encode_hierarchy_value(selected_path[index])
        elif require_selection:
            required.append(field)
        order.append(field)

        if index >= len(selected_path):
            if next_level := discovery.get("next_level"):
                next_field = _hierarchy_field(index + 1)
                properties[next_field] = _placeholder(
                    index + 1,
                    [field],
                    next_level["display_name"],
                )
                order.append(next_field)
            break

        selected = selected_path[index]
        if selected not in values:
            if index < locked_count:
                raise APIClientError(
                    "A fixed hierarchy selection is no longer available."
                )
            break
        path.append(selected)
        if "next_level" not in discovery:
            if len(selected_path) > len(path):
                raise APIClientError("The hierarchy path exceeds the final level.")
            break


def _effective_path(
    configuration: APIConfiguration,
    runtime_configuration: dict[str, Any],
) -> tuple[list[str | None], dict[str, Any]]:
    """Enforce the layer's fixed prefix and remove form fields from API config."""
    selected = hierarchy_path_from_fields(runtime_configuration)
    fixed = configuration.fixed_path
    if selected[: len(fixed)] != fixed[: len(selected)]:
        raise ValueError("The hierarchy selection conflicts with the semantic layer.")
    path = selected if len(selected) >= len(fixed) else list(fixed)
    additional = {
        key: value
        for key, value in runtime_configuration.items()
        if not key.startswith(HIERARCHY_FIELD_PREFIX)
    }
    return path, additional


@semantic_layer(
    id="semantic-layer-api",
    name="Semantic Layer API",
    description="Connect to a service implementing the Semantic Layer REST API.",
)
class APISemanticLayer(SemanticLayer[APIConfiguration, APISemanticView]):
    """Semantic-layer provider backed by the Semantic Layer REST API."""

    configuration_class = APIConfiguration

    def __init__(self, configuration: APIConfiguration):
        self.configuration = configuration

    @classmethod
    def from_configuration(
        cls,
        configuration: dict[str, Any],
    ) -> APISemanticLayer:
        return cls(APIConfiguration.model_validate(configuration))

    @classmethod
    def get_configuration_schema(
        cls,
        configuration: APIConfiguration | None = None,
    ) -> dict[str, Any]:
        schema = APIConfiguration.model_json_schema()
        properties = schema["properties"]
        properties.pop("fixed_path", None)
        schema["required"] = ["api_url"]
        properties["auth_type"]["x-dynamic"] = True
        properties["auth_type"]["x-dependsOn"] = ["auth_type"]
        properties["auth_type"]["x-enumNames"] = ["OAuth2", "access token"]

        auth_type = configuration.auth_type if configuration else "oauth2"
        auth_fields = (
            ["access_token"]
            if auth_type == "access_token"
            else [
                "client_id",
                "client_secret",
                "authorization_url",
                "token_url",
                "scope",
            ]
        )
        schema["required"].extend(
            ["access_token"]
            if auth_type == "access_token"
            else ["client_id", "client_secret"]
        )
        order = ["api_url", "auth_type", *auth_fields]
        schema["x-propertyOrder"] = order
        dependencies = ["api_url", "auth_type", *auth_fields[:1]]
        if auth_type == "oauth2":
            dependencies.append("client_secret")

        if configuration and _can_discover(configuration):
            try:
                client = APIClient(configuration)
                _add_discovery_fields(
                    schema,
                    client,
                    configuration.fixed_path,
                    0,
                    dependencies,
                    require_selection=False,
                )
            except (APIClientError, SemanticLayerAuthenticationRequiredError):
                for key in list(properties):
                    if key.startswith(HIERARCHY_FIELD_PREFIX):
                        properties.pop(key)
                order[:] = [
                    key for key in order if not key.startswith(HIERARCHY_FIELD_PREFIX)
                ]
                _add_unavailable_fields(
                    schema,
                    configuration.fixed_path,
                    dependencies,
                    "Unable to load discovery choices. Check the URL and authentication.",
                )
        else:
            _add_unavailable_fields(
                schema,
                configuration.fixed_path if configuration else [],
                dependencies,
                "Complete authentication to load discovery choices.",
            )
        order.append("request_timeout")
        return schema

    @classmethod
    def get_runtime_schema(
        cls,
        configuration: APIConfiguration,
        runtime_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": "object",
            "title": "Semantic Layer API runtime configuration",
            "additionalProperties": True,
            "properties": {},
            "x-propertyOrder": [],
        }
        client = APIClient(configuration)
        selected, _ = _effective_path(configuration, runtime_data or {})
        _add_discovery_fields(
            schema,
            client,
            selected,
            len(configuration.fixed_path),
            [],
            require_selection=True,
        )
        return schema

    @classmethod
    def get_configuration_actions(
        cls,
        configuration: APIConfiguration | None = None,
    ) -> list[SemanticLayerAction]:
        if configuration and configuration.auth_type == "access_token":
            return []
        connected = bool(
            configuration
            and configuration.client_id
            and configuration.client_secret
            and has_usable_credentials(configuration)
        )
        return [
            SemanticLayerAction(
                id=ACTION_ID,
                label="Connect account",
                status="connected" if connected else "required",
                required=True,
                message=(
                    "Your personal account is connected."
                    if connected
                    else "Connect your personal account to continue."
                ),
            )
        ]

    @classmethod
    def execute_configuration_action(
        cls,
        action_id: str,
        configuration: APIConfiguration | None,
        return_url: str | None = None,
    ) -> SemanticLayerActionResult:
        if action_id != ACTION_ID:
            raise ValueError(f"Unknown Semantic Layer API action: {action_id}")
        if configuration is None:
            raise ValueError("Complete the API configuration before connecting.")
        if configuration.auth_type != "oauth2":
            raise ValueError("OAuth2 is not the selected authentication method.")
        if not configuration.client_id or not configuration.client_secret:
            raise ValueError("Complete the OAuth2 client credentials first.")
        return SemanticLayerActionResult(
            redirect_url=start_authorization(configuration),
        )

    def get_semantic_views(
        self,
        runtime_configuration: dict[str, Any],
    ) -> set[APISemanticView]:
        path, additional = _effective_path(self.configuration, runtime_configuration)
        client = APIClient(self.configuration, additional)
        return {
            APISemanticView(str(summary["name"]), client, summary)
            for summary in client.list_views(path)
            if summary.get("name")
        }

    def get_semantic_view(
        self,
        name: str,
        additional_configuration: dict[str, Any],
    ) -> APISemanticView:
        path, additional = _effective_path(self.configuration, additional_configuration)
        client = APIClient(self.configuration, additional)
        if path and name not in {
            str(summary["name"]) for summary in client.list_views(path)
        }:
            raise ValueError("The semantic view is outside the selected hierarchy.")
        return APISemanticView(name, client)
