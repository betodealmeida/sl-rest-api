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

"""OAuth2 callback endpoint for the Semantic Layer API extension."""

from __future__ import annotations

from flask import redirect, request, Response
from flask_appbuilder.api import expose, permission_name, safe
from superset_core.rest_api.api import RestApi
from superset_core.rest_api.decorators import api

from superset.views.base_api import protect_read

from .oauth import exchange_authorization_code, OAuth2ExchangeError


@api(
    id="semantic_layer_api_oauth",
    name="Semantic Layer API OAuth2",
    description="OAuth2 callback for personal API credentials.",
    resource_name="oauth",
)
class OAuthAPI(RestApi):
    """Receive OAuth2 callbacks without exposing tokens to the browser."""

    openapi_spec_tag = "Semantic Layer API"
    class_permission_name = "SemanticLayer"

    @expose("/callback", methods=("GET",))
    @protect_read("SemanticLayer", "SemanticView")
    @safe
    @permission_name("read")
    def callback(self) -> Response:
        """Exchange an OAuth2 authorization code for a personal token."""
        if request.args.get("error"):
            return _message(
                "Authorization was denied. You can close this window.",
                400,
            )
        code = request.args.get("code", "")
        state = request.args.get("state", "")
        if not code or not state:
            return _message("The OAuth2 callback is incomplete.", 400)
        try:
            return_url = exchange_authorization_code(code, state)
        except OAuth2ExchangeError:
            return _message("Authorization could not be completed.", 400)
        if return_url is not None:
            return redirect(return_url)
        return _message("Account connected. You can close this window.", 200)


def _message(message: str, status: int) -> Response:
    body = (
        "<!doctype html><html><head><title>Semantic Layer API OAuth2</title></head>"
        f"<body><p>{message}</p></body></html>"
    )
    return Response(body, status=status, content_type="text/html; charset=utf-8")
