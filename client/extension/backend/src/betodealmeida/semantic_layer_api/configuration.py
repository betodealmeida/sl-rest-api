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

"""Configuration model for a Semantic Layer API service."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)


HIERARCHY_FIELD_PREFIX = "hierarchy_"
UNVERSIONED_LABEL = "unversioned"


def encode_hierarchy_value(value: str | None) -> str:
    """Represent nullable discovery values in Superset's string selects."""
    if value is None:
        return UNVERSIONED_LABEL
    if value == UNVERSIONED_LABEL or value.startswith("~"):
        return f"~{value}"
    return value


def decode_hierarchy_value(value: str) -> str | None:
    """Recover the exact API value, including a selected JSON null."""
    if value == UNVERSIONED_LABEL:
        return None
    return value[1:] if value.startswith("~") else value


def hierarchy_path_from_fields(
    value: dict[str, Any], *, strict: bool = True
) -> list[str | None]:
    """Read consecutive, selected hierarchy fields from form data."""
    selections: dict[int, str] = {}
    for key, selected in value.items():
        if not key.startswith(HIERARCHY_FIELD_PREFIX):
            continue
        suffix = key.removeprefix(HIERARCHY_FIELD_PREFIX)
        if not suffix.isdigit():
            raise ValueError("Invalid hierarchy field")
        if selected is None or selected == "":
            continue
        if not isinstance(selected, str):
            raise ValueError("Hierarchy selections must be strings")
        selections[int(suffix)] = selected
    if strict and selections and set(selections) != set(range(max(selections) + 1)):
        raise ValueError("Hierarchy selections must form a prefix")
    result: list[str | None] = []
    for index in range(len(selections)):
        if index not in selections:
            break
        result.append(decode_hierarchy_value(selections[index]))
    return result


class APIConfiguration(BaseModel):
    """Connection and OAuth2 client configuration for an API service."""

    model_config = ConfigDict(extra="forbid")

    api_url: str = Field(
        default="",
        title="Semantic Layer API URL",
        description=(
            "Base URL of the Semantic Layer REST API, including any "
            "path prefix but excluding /views."
        ),
        examples=["https://semantic-layer.example.com/api/semantic"],
    )
    auth_type: Literal["oauth2", "access_token"] = Field(
        default="oauth2",
        title="Authentication method",
    )
    client_id: str = Field(
        default="",
        title="OAuth2 client ID",
        description="OAuth2 client identifier registered for this Superset instance.",
    )
    client_secret: SecretStr = Field(
        default=SecretStr(""),
        title="OAuth2 client secret",
        description="OAuth2 client secret registered for this Superset instance.",
    )
    access_token: SecretStr = Field(
        default=SecretStr(""),
        title="Access token",
        description="Bearer token for the Semantic Layer API.",
    )
    authorization_url: str = Field(
        default="",
        title="Authorization URL",
        description="Defaults to <Semantic Layer API URL>/authorize.",
    )
    token_url: str = Field(
        default="",
        title="Token URL",
        description="Defaults to <Semantic Layer API URL>/token.",
    )
    scope: str = Field(
        default="",
        title="OAuth2 scopes",
        description="Space-delimited scopes requested from the OAuth2 provider.",
    )
    request_timeout: float = Field(
        default=60.0,
        ge=1.0,
        le=300.0,
        title="Request timeout",
        description="Timeout for Semantic Layer API requests, in seconds.",
    )
    fixed_path: list[str | None] = Field(default_factory=list, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def collect_hierarchy_fields(cls, value: Any, info: ValidationInfo) -> Any:
        """Fold the form's variable number of hierarchy fields into one path."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        partial = isinstance(info.context, dict) and info.context.get("partial")
        selected_path = hierarchy_path_from_fields(data, strict=not partial)
        for key in list(data):
            if key.startswith(HIERARCHY_FIELD_PREFIX):
                data.pop(key)
        if selected_path:
            data["fixed_path"] = selected_path
        return data

    @model_validator(mode="after")
    def require_complete_auth(self, info: ValidationInfo) -> APIConfiguration:
        """Allow partial form refreshes, but validate a saved connection."""
        if isinstance(info.context, dict) and info.context.get("partial"):
            return self
        if not self.api_url:
            raise ValueError("The Semantic Layer API URL is required")
        if self.auth_type == "oauth2" and (
            not self.client_id or not self.client_secret.get_secret_value()
        ):
            raise ValueError("OAuth2 client credentials are required")
        if (
            self.auth_type == "access_token"
            and not self.access_token.get_secret_value()
        ):
            raise ValueError("An access token is required")
        return self

    @field_validator("api_url", "authorization_url", "token_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        """Remove trailing slashes while preserving optional URL fields."""
        return value.rstrip("/") if value else value

    @property
    def resolved_authorization_url(self) -> str:
        """Return the explicitly configured or conventional authorization URL."""
        return self.authorization_url or f"{self.api_url}/authorize"

    @property
    def resolved_token_url(self) -> str:
        """Return the explicitly configured or conventional token URL."""
        return self.token_url or f"{self.api_url}/token"
