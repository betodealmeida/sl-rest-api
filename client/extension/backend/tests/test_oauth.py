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
from urllib.parse import parse_qs, urlparse

from flask import Flask

from netflix.quiver_semantic_layer.configuration import QuiverConfiguration
from netflix.quiver_semantic_layer import oauth


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get(self, key: str) -> object | None:
        return self.values.get(key)

    def set(self, key: str, value: object) -> None:
        self.values[key] = value

    def remove(self, key: str) -> None:
        self.values.pop(key, None)


def configuration() -> QuiverConfiguration:
    return QuiverConfiguration(
        api_url="https://quiver.example.test/api",
        client_id="client-id",
        client_secret="client-secret",
        scope="views.read",
    )


def context(secrets: MemorySecrets) -> SimpleNamespace:
    return SimpleNamespace(
        extension=SimpleNamespace(
            publisher="netflix",
            name="quiver-semantic-layer",
        ),
        storage=SimpleNamespace(secrets=secrets),
    )


def test_authorization_and_callback_store_a_personal_token(monkeypatch) -> None:
    secrets = MemorySecrets()
    monkeypatch.setattr(oauth, "get_context", lambda: context(secrets))
    monkeypatch.setattr(oauth.secrets, "token_urlsafe", lambda _: "state-value")
    app = Flask(__name__)

    with app.test_request_context("/", base_url="https://superset.example.test"):
        authorization_url = oauth.start_authorization(configuration())

    query = parse_qs(urlparse(authorization_url).query)
    assert query == {
        "response_type": ["code"],
        "client_id": ["client-id"],
        "redirect_uri": [
            "https://superset.example.test/extensions/netflix/"
            "quiver-semantic-layer/oauth/callback"
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
    oauth.exchange_authorization_code("authorization-code", "state-value")

    key = oauth.credential_secret_key(configuration())
    assert secrets.values[key] == {
        "access_token": "personal-access-token",
        "refresh_token": "personal-refresh-token",
    }
    assert oauth.PENDING_SECRET_KEY not in secrets.values


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
