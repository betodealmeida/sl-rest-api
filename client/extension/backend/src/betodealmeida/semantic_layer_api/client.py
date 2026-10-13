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

"""Authenticated HTTP client for the Semantic Layer REST API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import requests
from superset_core.semantic_layers.layer import (
    SemanticLayerAuthenticationRequiredError,
)

from .configuration import APIConfiguration
from .oauth import ACTION_ID, discard_token, get_access_token

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class APIClientError(RuntimeError):
    """Raised when the service returns a non-authentication API error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class APIClient:
    """Small protocol client bound to one configuration and Superset user."""

    def __init__(
        self,
        configuration: APIConfiguration,
        additional_configuration: Mapping[str, Any] | None = None,
    ) -> None:
        self.configuration = configuration
        self.additional_configuration: dict[str, Any] = {}
        if additional_configuration:
            self.additional_configuration.update(additional_configuration)

    def discover(self, path: list[str | None]) -> dict[str, Any]:
        """Discover the next level of the view hierarchy."""
        payload = self._post(
            "/views/discover/",
            {"runtime_configuration": self.additional_configuration, "path": path},
        )
        if (
            not isinstance(payload, Mapping)
            or not isinstance(payload.get("level"), Mapping)
            or not isinstance(payload["level"].get("id"), str)
            or not payload["level"]["id"]
            or not isinstance(payload["level"].get("display_name"), str)
            or not payload["level"]["display_name"]
            or "has_next" in payload
            or not isinstance(payload.get("values"), list)
            or any(
                value is not None and (not isinstance(value, str) or not value)
                for value in payload["values"]
            )
            or (
                "next_level" in payload
                and (
                    not isinstance(payload["next_level"], Mapping)
                    or not isinstance(payload["next_level"].get("id"), str)
                    or not payload["next_level"]["id"]
                    or not isinstance(
                        payload["next_level"].get("display_name"), str
                    )
                    or not payload["next_level"]["display_name"]
                )
            )
        ):
            raise APIClientError("The API returned invalid discovery metadata.")
        return dict(payload)

    def list_views(self, path: list[str | None]) -> list[dict[str, Any]]:
        """Return lightweight semantic-view summaries."""
        payload = self._post(
            "/views/",
            {"runtime_configuration": self.additional_configuration, "path": path},
        )
        if not isinstance(payload, list):
            raise APIClientError("The API returned an invalid view list.")
        return [dict(item) for item in payload if isinstance(item, Mapping)]

    def get_view(self, name: str) -> dict[str, Any]:
        """Return the complete metadata for one semantic view."""
        payload = self._post(
            f"/views/{name}",
            {"additional_configuration": self.additional_configuration},
        )
        if not isinstance(payload, Mapping):
            raise APIClientError("The API returned invalid view metadata.")
        return dict(payload)

    def query(self, name: str, query: Mapping[str, Any]) -> dict[str, Any]:
        """Execute a semantic query."""
        return self._result(
            f"/views/{name}/query",
            {"query": query},
        )

    def row_count(self, name: str, query: Mapping[str, Any]) -> dict[str, Any]:
        """Execute a semantic row-count query."""
        return self._result(
            f"/views/{name}/row-count",
            {"query": query},
        )

    def values(
        self,
        name: str,
        dimension: str,
        filters: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Fetch distinct values for a dimension."""
        return self._result(
            f"/views/{name}/values",
            {"dimension": dimension, "filters": filters},
        )

    def compatible(
        self,
        name: str,
        kind: str,
        selected_metrics: list[str],
        selected_dimensions: list[str],
    ) -> list[dict[str, Any]]:
        """Return compatible metric or dimension payloads."""
        payload = self._post(
            f"/views/{name}/compatible-{kind}",
            {
                "additional_configuration": self.additional_configuration,
                "selected_metrics": selected_metrics,
                "selected_dimensions": selected_dimensions,
            },
        )
        if not isinstance(payload, list):
            raise APIClientError(f"The API returned invalid compatible {kind}.")
        return [dict(item) for item in payload if isinstance(item, Mapping)]

    def _result(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = self._post(
            path,
            {"additional_configuration": self.additional_configuration, **body},
        )
        if not isinstance(payload, Mapping):
            raise APIClientError("The API returned an invalid semantic result.")
        return dict(payload)

    def _post(
        self,
        path: str,
        body: Mapping[str, Any],
    ) -> Any:
        if self.configuration.auth_type == "access_token":
            secret = self.configuration.access_token
            if secret is None:
                raise APIClientError("An access token is required.")
            token = secret.get_secret_value()
        else:
            token = get_access_token(self.configuration)
        try:
            response = requests.post(
                f"{self.configuration.api_url}{path}",
                json=_json_value(dict(body)),
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.configuration.request_timeout,
                allow_redirects=False,
            )
        except requests.RequestException as ex:
            raise APIClientError("Unable to reach the Semantic Layer API.") from ex

        if response.status_code == 401 or response.status_code in _REDIRECT_STATUSES:
            if self.configuration.auth_type == "oauth2":
                discard_token(self.configuration)
                raise SemanticLayerAuthenticationRequiredError(
                    ACTION_ID,
                    "Semantic Layer API authentication is required.",
                )
            raise APIClientError("The Semantic Layer API rejected the access token.")
        if response.status_code == 404:
            raise APIClientError(
                _response_detail(response, "Semantic view not found."),
                status_code=404,
            )
        try:
            response.raise_for_status()
            return response.json()
        except requests.RequestException as ex:
            raise APIClientError(
                _response_detail(response, "Semantic Layer API request failed.")
            ) from ex
        except ValueError as ex:
            raise APIClientError("The API returned a non-JSON response.") from ex


def _response_detail(response: requests.Response, fallback: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        return fallback
    return (
        str(payload.get("detail", fallback))
        if isinstance(payload, Mapping)
        else fallback
    )


def _json_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    return cast(Any, value)
