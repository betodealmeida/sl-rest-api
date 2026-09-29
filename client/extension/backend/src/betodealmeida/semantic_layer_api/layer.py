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

from superset_core.semantic_layers.decorators import semantic_layer
from superset_core.semantic_layers.layer import (
    SemanticLayer,
    SemanticLayerAction,
    SemanticLayerActionResult,
)

from .client import APIClient
from .configuration import APIConfiguration
from .oauth import ACTION_ID, has_usable_credentials, start_authorization
from .view import APISemanticView


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
        return APIConfiguration.model_json_schema()

    @classmethod
    def get_runtime_schema(
        cls,
        configuration: APIConfiguration,
        runtime_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "title": "Semantic Layer API runtime configuration",
            "additionalProperties": True,
            "properties": {},
        }

    @classmethod
    def get_configuration_actions(
        cls,
        configuration: APIConfiguration | None = None,
    ) -> list[SemanticLayerAction]:
        connected = bool(configuration and has_usable_credentials(configuration))
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
        return SemanticLayerActionResult(
            redirect_url=start_authorization(configuration),
        )

    def get_semantic_views(
        self,
        runtime_configuration: dict[str, Any],
    ) -> set[APISemanticView]:
        client = APIClient(self.configuration, runtime_configuration)
        return {
            APISemanticView(str(summary["name"]), client, summary)
            for summary in client.list_views()
            if summary.get("name")
        }

    def get_semantic_view(
        self,
        name: str,
        additional_configuration: dict[str, Any],
    ) -> APISemanticView:
        return APISemanticView(
            name,
            APIClient(self.configuration, additional_configuration),
        )
