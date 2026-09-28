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

"""Superset semantic-layer contribution for Quiver."""

from __future__ import annotations

from typing import Any

from superset_core.semantic_layers.decorators import semantic_layer
from superset_core.semantic_layers.layer import (
    SemanticLayer,
    SemanticLayerAction,
    SemanticLayerActionResult,
)

from .client import QuiverClient
from .configuration import QuiverConfiguration
from .oauth import ACTION_ID, has_usable_credentials, start_authorization
from .view import QuiverSemanticView


@semantic_layer(
    id="quiver",
    name="Quiver",
    description="Netflix Quiver gateway to the Semantic Layer REST API.",
)
class QuiverSemanticLayer(SemanticLayer[QuiverConfiguration, QuiverSemanticView]):
    """Semantic-layer provider backed by Quiver's REST gateway."""

    configuration_class = QuiverConfiguration

    def __init__(self, configuration: QuiverConfiguration):
        self.configuration = configuration

    @classmethod
    def from_configuration(
        cls,
        configuration: dict[str, Any],
    ) -> QuiverSemanticLayer:
        return cls(QuiverConfiguration.model_validate(configuration))

    @classmethod
    def get_configuration_schema(
        cls,
        configuration: QuiverConfiguration | None = None,
    ) -> dict[str, Any]:
        return QuiverConfiguration.model_json_schema()

    @classmethod
    def get_runtime_schema(
        cls,
        configuration: QuiverConfiguration,
        runtime_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "title": "Quiver runtime configuration",
            "additionalProperties": True,
            "properties": {},
        }

    @classmethod
    def get_configuration_actions(
        cls,
        configuration: QuiverConfiguration | None = None,
    ) -> list[SemanticLayerAction]:
        connected = bool(configuration and has_usable_credentials(configuration))
        return [
            SemanticLayerAction(
                id=ACTION_ID,
                label="Connect to Quiver",
                status="connected" if connected else "required",
                required=True,
                message=(
                    "Your personal Quiver account is connected."
                    if connected
                    else "Connect your personal Quiver account to continue."
                ),
            )
        ]

    @classmethod
    def execute_configuration_action(
        cls,
        action_id: str,
        configuration: QuiverConfiguration | None,
        return_url: str | None = None,
    ) -> SemanticLayerActionResult:
        if action_id != ACTION_ID:
            raise ValueError(f"Unknown Quiver action: {action_id}")
        if configuration is None:
            raise ValueError("Complete the Quiver configuration before connecting.")
        return SemanticLayerActionResult(
            redirect_url=start_authorization(configuration),
        )

    def get_semantic_views(
        self,
        runtime_configuration: dict[str, Any],
    ) -> set[QuiverSemanticView]:
        client = QuiverClient(self.configuration, runtime_configuration)
        return {
            QuiverSemanticView(str(summary["name"]), client, summary)
            for summary in client.list_views()
            if summary.get("name")
        }

    def get_semantic_view(
        self,
        name: str,
        additional_configuration: dict[str, Any],
    ) -> QuiverSemanticView:
        return QuiverSemanticView(
            name,
            QuiverClient(self.configuration, additional_configuration),
        )
