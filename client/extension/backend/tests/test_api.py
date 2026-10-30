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

import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from betodealmeida.semantic_layer_api import api, oauth
from flask import Flask, g


@pytest.mark.parametrize(
    "return_url", ["https://superset.example.test/complete/flow", None]
)
def test_callback_redirects_only_after_token_exchange(
    monkeypatch: pytest.MonkeyPatch, return_url: str | None
) -> None:
    """A completed exchange redirects to the host; older callers get a success page."""
    exchange = Mock(return_value=return_url)
    monkeypatch.setattr(api, "exchange_authorization_code", exchange)
    app = Flask(__name__)
    with app.test_request_context(
        "/callback?code=authorization-code&state=state-value"
    ):
        response = inspect.unwrap(api.OAuthAPI.callback)(api.OAuthAPI())
    exchange.assert_called_once_with("authorization-code", "state-value")
    if return_url:
        assert response.status_code == 302
        assert response.location == return_url
    else:
        assert response.status_code == 200
        assert "Account connected" in response.get_data(as_text=True)


@pytest.mark.parametrize("failure", ["denied", "incomplete", "exchange"])
def test_callback_failure_never_signals_completion(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Denied or failed authentication must not cause the original tab to retry."""
    exchange = Mock(side_effect=oauth.OAuth2ExchangeError("Unable to exchange"))
    monkeypatch.setattr(api, "exchange_authorization_code", exchange)
    app = Flask(__name__)
    query = {
        "denied": "error=access_denied&state=state-value",
        "incomplete": "state=state-value",
        "exchange": "code=code&state=state-value",
    }[failure]
    with app.test_request_context(f"/callback?{query}"):
        response = inspect.unwrap(api.OAuthAPI.callback)(api.OAuthAPI())
    assert response.status_code == 400
    assert response.location is None
    if failure != "exchange":
        exchange.assert_not_called()


@pytest.mark.parametrize("view_granted", [True, False])
def test_callback_accepts_view_read_permission(
    monkeypatch: pytest.MonkeyPatch, view_granted: bool
) -> None:
    """A view reader can finish personal OAuth without the layer read permission."""
    manager = Mock()
    manager.is_item_public.return_value = False
    manager.has_access.side_effect = (
        lambda permission, resource: view_granted and resource == "SemanticView"
    )
    monkeypatch.setattr("superset.views.base_api.security_manager", manager)
    exchange = Mock(return_value="http://localhost/complete/flow")
    monkeypatch.setattr(api, "exchange_authorization_code", exchange)
    app = Flask(__name__)
    endpoint = api.OAuthAPI()
    endpoint.base_permissions = ["can_read"]
    with app.test_request_context("/callback?code=code&state=state-value"):
        g._login_user = SimpleNamespace(is_authenticated=True)
        response = endpoint.callback()
    assert response.status_code == (302 if view_granted else 403)
    if view_granted:
        exchange.assert_called_once_with("code", "state-value")
    else:
        exchange.assert_not_called()
