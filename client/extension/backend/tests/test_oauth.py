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

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
from betodealmeida.semantic_layer_api import oauth
from betodealmeida.semantic_layer_api.configuration import APIConfiguration
from flask import Flask


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get(self, key: str) -> object | None:
        return self.values.get(key)

    def set(self, key: str, value: object) -> None:
        self.values[key] = value

    def remove(self, key: str) -> None:
        self.values.pop(key, None)


def configuration() -> APIConfiguration:
    return APIConfiguration(
        api_url="https://semantic-layer.example.test/api",
        client_id="client-id",
        client_secret="client-secret",  # noqa: S106
        scope="views.read",
    )


def context(secrets: MemorySecrets) -> SimpleNamespace:
    return SimpleNamespace(
        extension=SimpleNamespace(
            publisher="betodealmeida",
            name="semantic-layer-api",
        ),
        storage=SimpleNamespace(secrets=secrets),
    )


def test_authorization_and_callback_store_a_personal_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = MemorySecrets()
    monkeypatch.setattr(oauth, "get_context", lambda: context(secrets))
    monkeypatch.setattr(oauth.secrets, "token_urlsafe", lambda _: "state-value")
    app = Flask(__name__)

    with app.test_request_context("/", base_url="https://superset.example.test"):
        return_url = (
            "https://superset.example.test/api/v1/semantic_layer/oauth2/complete/flow"
        )
        authorization_url = oauth.start_authorization(configuration(), return_url)

    query = parse_qs(urlparse(authorization_url).query)
    assert query == {
        "response_type": ["code"],
        "client_id": ["client-id"],
        "redirect_uri": [
            "https://superset.example.test/extensions/betodealmeida/"
            "semantic-layer-api/oauth/callback"
        ],
        "state": ["state-value"],
        "scope": ["views.read"],
    }

    monkeypatch.setattr(
        oauth,
        "_request_token",
        lambda url, data, timeout: {
            "access_token": "personal-access-token",
            "refresh_token": "personal-refresh-token",
        },
    )
    assert (
        secrets.values[oauth.pending_secret_key("state-value")]["return_url"]
        == return_url
    )
    assert (
        oauth.exchange_authorization_code("authorization-code", "state-value")
        == return_url
    )

    key = oauth.credential_secret_key(configuration())
    assert secrets.values[key] == {
        "access_token": "personal-access-token",
        "refresh_token": "personal-refresh-token",
    }
    assert oauth.pending_secret_key("state-value") not in secrets.values


def test_expired_access_token_is_refreshed(monkeypatch) -> None:
    secrets = MemorySecrets()
    config = configuration()
    secrets.set(
        oauth.credential_secret_key(config),
        {
            "access_token": "expired",
            "refresh_token": "refresh-token",
            "expires_at": 0,
        },
    )
    monkeypatch.setattr(oauth, "get_context", lambda: context(secrets))
    monkeypatch.setattr(
        oauth,
        "_request_token",
        lambda url, data, timeout: {"access_token": "refreshed"},
    )

    assert oauth.get_access_token(config) == "refreshed"
    assert secrets.values[oauth.credential_secret_key(config)] == {
        "access_token": "refreshed",
        "refresh_token": "refresh-token",
    }


def test_credential_key_is_stable_and_separates_clients() -> None:
    first = configuration()
    same = configuration()
    other = configuration().model_copy(update={"client_id": "another-client"})

    assert oauth.credential_secret_key(first) == oauth.credential_secret_key(same)
    assert oauth.credential_secret_key(first) != oauth.credential_secret_key(other)


def test_concurrent_authorizations_keep_their_completion_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completing one attempt must not consume another tab's pending state."""
    secrets = MemorySecrets()
    monkeypatch.setattr(oauth, "get_context", lambda: context(secrets))
    states = iter(["first-state", "second-state"])
    monkeypatch.setattr(oauth.secrets, "token_urlsafe", lambda _: next(states))
    monkeypatch.setattr(
        oauth, "_request_token", lambda *args: {"access_token": "token"}
    )
    app = Flask(__name__)
    first_url = "https://superset.example.test/complete/first"
    second_url = "https://superset.example.test/complete/second"
    with app.test_request_context("/", base_url="https://superset.example.test"):
        oauth.start_authorization(configuration(), first_url)
        oauth.start_authorization(configuration(), second_url)
    assert oauth.exchange_authorization_code("code-1", "first-state") == first_url
    assert oauth.pending_secret_key("second-state") in secrets.values
    assert oauth.exchange_authorization_code("code-2", "second-state") == second_url
    assert not any(key.startswith(oauth.PENDING_KEY_PREFIX) for key in secrets.values)


@pytest.mark.parametrize("failure", ["unknown", "mismatch", "expired"])
def test_invalid_state_does_not_exchange_a_code(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Invalid or expired state cannot store tokens or produce a completion URL."""
    secrets = MemorySecrets()
    monkeypatch.setattr(oauth, "get_context", lambda: context(secrets))
    monkeypatch.setattr(oauth.secrets, "token_urlsafe", lambda _: "state-value")
    request_token = Mock()
    monkeypatch.setattr(oauth, "_request_token", request_token)
    app = Flask(__name__)
    with app.test_request_context("/"):
        oauth.start_authorization(configuration())
    pending_key = oauth.pending_secret_key("state-value")
    pending = secrets.values[pending_key]
    if failure == "expired":
        pending["created_at"] = 0
    elif failure == "mismatch":
        pending["state"] = "different-state"
    state = "unknown-state" if failure == "unknown" else "state-value"
    with pytest.raises(oauth.OAuth2ExchangeError):
        oauth.exchange_authorization_code("code", state)
    request_token.assert_not_called()
    assert oauth.credential_secret_key(configuration()) not in secrets.values
    if failure == "expired":
        assert pending_key not in secrets.values


def test_callback_state_is_single_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """A consumed authorization attempt cannot be replayed."""
    secrets = MemorySecrets()
    monkeypatch.setattr(oauth, "get_context", lambda: context(secrets))
    monkeypatch.setattr(oauth.secrets, "token_urlsafe", lambda _: "state-value")
    request_token = Mock(return_value={"access_token": "token"})
    monkeypatch.setattr(oauth, "_request_token", request_token)
    app = Flask(__name__)
    with app.test_request_context("/"):
        oauth.start_authorization(configuration())
    assert oauth.exchange_authorization_code("code", "state-value") is None
    with pytest.raises(oauth.OAuth2ExchangeError):
        oauth.exchange_authorization_code("code", "state-value")
    request_token.assert_called_once()


@pytest.mark.parametrize(
    "return_url", ["https://other.example/complete", "//other.example/complete"]
)
def test_authorization_rejects_an_external_completion_url(
    monkeypatch: pytest.MonkeyPatch, return_url: str
) -> None:
    """Only the initiating Superset origin can receive a completion redirect."""
    secrets = MemorySecrets()
    monkeypatch.setattr(oauth, "get_context", lambda: context(secrets))
    app = Flask(__name__)
    with app.test_request_context("/", base_url="https://superset.example.test"):
        with pytest.raises(ValueError, match="Superset origin"):
            oauth.start_authorization(configuration(), return_url)
    assert secrets.values == {}
