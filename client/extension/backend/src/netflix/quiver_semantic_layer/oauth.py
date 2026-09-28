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

"""OAuth2 authorization-code flow and personal token storage."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

import requests
from flask import request
from superset_core.extensions.context import get_context
from superset_core.semantic_layers.layer import (
    SemanticLayerAuthenticationRequiredError,
)

from .configuration import QuiverConfiguration

ACTION_ID = "oauth2"
PENDING_SECRET_KEY = "oauth2:pending"
TOKEN_EXPIRY_SKEW_SECONDS = 30
PENDING_LIFETIME_SECONDS = 600


class OAuth2ExchangeError(RuntimeError):
    """Raised when the OAuth2 provider rejects a callback or token request."""


def credential_secret_key(configuration: QuiverConfiguration) -> str:
    """Return a stable key shared by layers using the same Quiver identity."""
    identity = f"{configuration.api_url}\0{configuration.client_id}".encode()
    digest = hashlib.sha256(identity).hexdigest()
    return f"oauth2:token:{digest}"


def get_stored_token(configuration: QuiverConfiguration) -> dict[str, Any] | None:
    """Return the current user's token record for this Quiver identity."""
    value = get_context().storage.secrets.get(credential_secret_key(configuration))
    return dict(value) if isinstance(value, Mapping) else None


def has_usable_credentials(configuration: QuiverConfiguration) -> bool:
    """Return whether a token or refresh token can authenticate the user."""
    token = get_stored_token(configuration)
    return bool(
        token
        and isinstance(token.get("access_token"), str)
        and (not _is_expired(token) or isinstance(token.get("refresh_token"), str))
    )


def start_authorization(configuration: QuiverConfiguration) -> str:
    """Persist an OAuth2 transaction and return its authorization URL."""
    context = get_context()
    state = secrets.token_urlsafe(32)
    extension = context.extension
    callback_path = f"extensions/{extension.publisher}/{extension.name}/oauth/callback"
    redirect_uri = f"{request.url_root.rstrip('/')}/{callback_path}"
    context.storage.secrets.set(
        PENDING_SECRET_KEY,
        {
            "state": state,
            "created_at": time.time(),
            "credential_key": credential_secret_key(configuration),
            "client_id": configuration.client_id,
            "client_secret": configuration.client_secret.get_secret_value(),
            "token_url": configuration.resolved_token_url,
            "redirect_uri": redirect_uri,
            "request_timeout": configuration.request_timeout,
        },
    )
    parameters = {
        "response_type": "code",
        "client_id": configuration.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if configuration.scope:
        parameters["scope"] = configuration.scope
    return f"{configuration.resolved_authorization_url}?{urlencode(parameters)}"


def exchange_authorization_code(code: str, state: str) -> None:
    """Validate the callback, exchange its code, and store the personal token."""
    secrets_state = get_context().storage.secrets
    value = secrets_state.get(PENDING_SECRET_KEY)
    pending = dict(value) if isinstance(value, Mapping) else None
    if not pending or not secrets.compare_digest(str(pending.get("state", "")), state):
        raise OAuth2ExchangeError("The OAuth2 state is missing or invalid.")
    created_at = pending.get("created_at")
    if not isinstance(created_at, (int, float)) or (
        time.time() - created_at > PENDING_LIFETIME_SECONDS
    ):
        secrets_state.remove(PENDING_SECRET_KEY)
        raise OAuth2ExchangeError("The OAuth2 authorization request has expired.")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": str(pending["client_id"]),
        "client_secret": str(pending["client_secret"]),
        "redirect_uri": str(pending["redirect_uri"]),
    }
    token = _request_token(
        str(pending["token_url"]),
        data,
        float(pending.get("request_timeout", 60.0)),
    )
    secrets_state.set(str(pending["credential_key"]), token)
    secrets_state.remove(PENDING_SECRET_KEY)


def get_access_token(configuration: QuiverConfiguration) -> str:
    """Return a valid access token, refreshing it when necessary."""
    secrets_state = get_context().storage.secrets
    key = credential_secret_key(configuration)
    token = get_stored_token(configuration)
    if not token or not isinstance(token.get("access_token"), str):
        raise SemanticLayerAuthenticationRequiredError(ACTION_ID)
    if not _is_expired(token):
        return str(token["access_token"])

    refresh_token = token.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        secrets_state.remove(key)
        raise SemanticLayerAuthenticationRequiredError(
            ACTION_ID,
            "Your Quiver authentication has expired.",
        )
    try:
        refreshed = _request_token(
            configuration.resolved_token_url,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": configuration.client_id,
                "client_secret": configuration.client_secret.get_secret_value(),
            },
            configuration.request_timeout,
        )
    except OAuth2ExchangeError as ex:
        secrets_state.remove(key)
        raise SemanticLayerAuthenticationRequiredError(
            ACTION_ID,
            "Your Quiver authentication must be renewed.",
        ) from ex
    if "refresh_token" not in refreshed:
        refreshed["refresh_token"] = refresh_token
    secrets_state.set(key, refreshed)
    return str(refreshed["access_token"])


def discard_token(configuration: QuiverConfiguration) -> None:
    """Remove the current user's token after an authentication rejection."""
    get_context().storage.secrets.remove(credential_secret_key(configuration))


def _request_token(url: str, data: Mapping[str, str], timeout: float) -> dict[str, Any]:
    try:
        response = requests.post(url, data=dict(data), timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as ex:
        raise OAuth2ExchangeError("The OAuth2 token request failed.") from ex
    if not isinstance(payload, Mapping) or not isinstance(
        payload.get("access_token"), str
    ):
        raise OAuth2ExchangeError("The OAuth2 token response has no access token.")
    token = dict(payload)
    expires_in = token.get("expires_in")
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
        token["expires_at"] = time.time() + float(expires_in)
    return token


def _is_expired(token: Mapping[str, Any]) -> bool:
    expires_at = token.get("expires_at")
    return isinstance(expires_at, (int, float)) and (
        time.time() >= float(expires_at) - TOKEN_EXPIRY_SKEW_SECONDS
    )
