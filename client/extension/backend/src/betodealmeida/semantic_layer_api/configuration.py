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

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class APIConfiguration(BaseModel):
    """Connection and OAuth2 client configuration for an API service."""

    model_config = ConfigDict(extra="forbid")

    api_url: str = Field(
        min_length=1,
        title="Semantic Layer API URL",
        description=(
            "Base URL of the Semantic Layer REST API, including any "
            "path prefix but excluding /views."
        ),
        examples=["https://semantic-layer.example.com/api/semantic"],
    )
    client_id: str = Field(
        min_length=1,
        title="OAuth2 client ID",
        description="OAuth2 client identifier registered for this Superset instance.",
    )
    client_secret: SecretStr = Field(
        min_length=1,
        title="OAuth2 client secret",
        description="OAuth2 client secret registered for this Superset instance.",
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
