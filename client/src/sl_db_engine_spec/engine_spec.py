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
"""
An interface to any server implementing the Semantic Layer REST API.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TYPE_CHECKING, TypedDict

from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from flask import g
from marshmallow import fields, Schema
from shillelagh.exceptions import UnauthenticatedError
from sqlalchemy.engine.url import URL

from superset.databases.types import EncryptedString
from superset.databases.utils import make_url_safe
from superset.db_engine_specs.base import MetricType
from superset.db_engine_specs.shillelagh import ShillelaghEngineSpec
from superset.errors import ErrorLevel, SupersetError, SupersetErrorType
from superset.exceptions import OAuth2TokenRefreshError
from superset.utils import json

from sl_db_engine_spec.metadata import d3format_from_metadata

if TYPE_CHECKING:
    from sqlalchemy.engine.reflection import Inspector

    from superset.models.core import Database
    from superset.sql.parse import Table
    from superset.superset_typing import ResultSetColumnType


SELECT_STAR_MESSAGE = (
    "The Semantic Layer API does not support data preview, since the view is a "
    "virtual table that is not materialized. An administrator should configure "
    'the database in Apache Superset so that the "Disable SQL Lab data preview '
    'queries" option under "Advanced" → "SQL Lab" is enabled.'
)

_TRUTHY = {"1", "true", "yes", "on"}


def _semantic_metadata(column: Mapping[str, Any]) -> dict[str, Any]:
    metadata = column.get("semantic_metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


ma_plugin = MarshmallowPlugin()


class SemanticAPIParametersSchema(Schema):
    """Form schema for the Semantic Layer API connection wizard."""

    host = fields.Str(
        required=True,
        metadata={"description": "Hostname of the Semantic Layer API server."},
    )
    port = fields.Int(
        required=False,
        allow_none=True,
        metadata={"description": "Optional port (omit to use the URL default)."},
    )
    encryption = fields.Bool(
        load_default=False,
        metadata={"description": "Use HTTPS to reach the server."},
    )
    http_path = fields.Str(
        required=False,
        allow_none=True,
        metadata={
            "description": (
                "Base path where the Semantic Layer REST API is mounted on "
                "the server, e.g. ``/api/datajunction/semantic``. Leave "
                "blank if the API is mounted at the root."
            ),
        },
    )
    additional_configuration = fields.Dict(
        required=False,
        allow_none=True,
        metadata={
            "description": (
                "Per-tenant JSON object forwarded to the server on every call. "
                "Sent as ``runtime_configuration`` when listing views and as "
                "``additional_configuration`` for each view."
            ),
        },
    )
    oauth2_client_info = EncryptedString(
        required=False,
        allow_none=True,
        metadata={
            "description": (
                "OAuth2 client credentials, as a JSON object with ``id`` and "
                "``secret``. ``authorization_request_uri`` and "
                "``token_request_uri`` are auto-filled from the server host "
                "if not supplied."
            ),
            "default": {"id": "", "secret": "", "scope": ""},
        },
    )


class SemanticAPIParametersType(TypedDict, total=False):
    host: str
    port: int | None
    encryption: bool
    http_path: str | None
    additional_configuration: dict[str, Any] | None
    oauth2_client_info: str | None


class SemanticAPIPropertiesType(TypedDict, total=False):
    parameters: SemanticAPIParametersType
    masked_encrypted_extra: str


class SemanticAPIEngineSpec(ShillelaghEngineSpec):
    """
    Engine for the Semantic Layer REST API.

    Connection URL::

        semanticapi://<host>[:port]/[?encryption=true][&http_path=<path>]

    OAuth2 is supported. The database's ``encrypted_extra`` should look like::

        {
          "oauth2_client_info": {
            "id": "demo-client",
            "secret": "demo-secret",
            "scope": "",
            "authorization_request_uri": "http://<host>:<port>/authorize",
            "token_request_uri":         "http://<host>:<port>/token"
          }
        }

    Per-tenant ``additional_configuration`` is forwarded through
    ``extra.engine_params.connect_args.additional_configuration``.
    """

    engine = "semanticapi"
    engine_name = "Semantic Layer API"
    default_driver = "apsw"
    sqlalchemy_uri_placeholder = (
        "semanticapi://<host>[:port]/?encryption=<true|false>&http_path=<path>"
    )

    supports_catalog = True
    supports_dynamic_catalog = True
    supports_dynamic_schema = True
    # Catalogs and schemas organize view discovery. SQL still addresses each
    # view by its full, dot-delimited name as one quoted table identifier.
    quote_table_includes_schema = False
    try_remove_schema_from_table_name = False

    parameters_schema = SemanticAPIParametersSchema()

    # OAuth 2.0 — the authorisation and token URIs live on each database's
    # ``encrypted_extra.oauth2_client_info`` since they're derived from that
    # database's server.
    supports_oauth2 = True
    oauth2_exception = (UnauthenticatedError, OAuth2TokenRefreshError)
    oauth2_token_request_type = "data"  # noqa: S105

    # pylint: disable=invalid-name
    encrypted_extra_sensitive_fields = {
        "$.oauth2_client_info.secret": "OAuth2 Client Secret",
    }

    @staticmethod
    def update_params_from_encrypted_extra(
        database: Database,
        params: dict[str, Any],
    ) -> None:
        """
        Superset's default copies every ``encrypted_extra`` key into engine
        kwargs. ``oauth2_client_info`` is for Superset's own OAuth2 dance, not
        for the dialect, so we drop it before it reaches ``create_engine``.
        """
        ShillelaghEngineSpec.update_params_from_encrypted_extra(database, params)
        params.pop("oauth2_client_info", None)

    @classmethod
    def build_sqlalchemy_uri(
        cls,
        parameters: SemanticAPIParametersType,
        encrypted_extra: dict[str, Any] | None = None,
    ) -> str:
        """
        Turn form parameters into a ``semanticapi://`` URL.

        ``additional_configuration`` is JSON-encoded onto the query string,
        and the OAuth2 ``authorization_request_uri`` / ``token_request_uri``
        are filled in (if missing) from the server's host:port.
        """
        host = parameters.get("host") or ""
        port = parameters.get("port")
        encryption = bool(parameters.get("encryption"))

        if encrypted_extra is None:
            encrypted_extra = {}

        # Promote ``parameters.oauth2_client_info`` into ``encrypted_extra``
        # when missing. The frontend sends it in ``parameters`` during
        # ``validate_parameters`` (no ``masked_encrypted_extra`` in that call),
        # which would otherwise leave the ephemeral database's
        # ``encrypted_extra`` empty and ``is_oauth2_enabled()`` returning False
        # — masking what is in fact an OAuth2 connection.
        if (
            "oauth2_client_info" not in encrypted_extra
            and parameters.get("oauth2_client_info")
        ):
            client_info = parameters["oauth2_client_info"]
            if isinstance(client_info, str):
                try:
                    client_info = json.loads(client_info)
                except (TypeError, ValueError):
                    client_info = None
            if isinstance(client_info, dict):
                encrypted_extra["oauth2_client_info"] = client_info

        query: dict[str, str] = {}
        if encryption:
            query["encryption"] = "true"
        if http_path := parameters.get("http_path"):
            query["http_path"] = http_path
        if config := parameters.get("additional_configuration"):
            query["additional_configuration"] = (
                config if isinstance(config, str) else json.dumps(config)
            )

        if oauth2 := encrypted_extra.get("oauth2_client_info"):
            scheme = "https" if encryption else "http"
            netloc = f"{host}:{port}" if port else host
            base = f"{scheme}://{netloc}"
            oauth2.setdefault("authorization_request_uri", f"{base}/authorize")
            oauth2.setdefault("token_request_uri", f"{base}/token")
            oauth2.setdefault("scope", "")

        return str(URL.create(cls.engine, host=host, port=port, query=query))

    @classmethod
    def get_parameters_from_uri(
        cls,
        uri: str,
        encrypted_extra: dict[str, Any] | None = None,
    ) -> SemanticAPIParametersType:
        """
        Inverse of :meth:`build_sqlalchemy_uri` for repopulating the form.
        """
        url = make_url_safe(uri)
        parameters: SemanticAPIParametersType = {
            "host": url.host or "",
            "port": url.port,
            "encryption": str(url.query.get("encryption", "")).lower() in _TRUTHY,
        }
        if http_path := url.query.get("http_path"):
            parameters["http_path"] = http_path
        if raw := url.query.get("additional_configuration"):
            try:
                parameters["additional_configuration"] = json.loads(raw)
            except json.JSONDecodeError:
                parameters["additional_configuration"] = None
        if encrypted_extra and "oauth2_client_info" in encrypted_extra:
            parameters["oauth2_client_info"] = encrypted_extra["oauth2_client_info"]
        return parameters

    @classmethod
    def parameters_json_schema(cls) -> Any:
        """Return the form's OpenAPI schema, used by the frontend to render it."""
        # imported lazily because ``superset.databases.schemas`` touches the
        # Flask app context at import time.
        from superset.databases.schemas import encrypted_field_properties

        if not cls.parameters_schema:
            return None
        spec = APISpec(
            title="Database Parameters",
            version="1.0.0",
            openapi_version="3.0.0",
            plugins=[ma_plugin],
        )
        ma_plugin.init_spec(spec)
        ma_plugin.converter.add_attribute_function(encrypted_field_properties)
        spec.components.schema(cls.__name__, schema=cls.parameters_schema)
        return spec.to_dict()["components"]["schemas"][cls.__name__]

    @classmethod
    def validate_parameters(
        cls,
        properties: SemanticAPIPropertiesType,
    ) -> list[SupersetError]:
        """Surface missing ``host`` (the only field we strictly require)."""
        errors: list[SupersetError] = []
        parameters = properties.get("parameters", {})
        if not parameters.get("host"):
            errors.append(
                SupersetError(
                    message="Host is required.",
                    error_type=SupersetErrorType.CONNECTION_MISSING_PARAMETERS_ERROR,
                    level=ErrorLevel.WARNING,
                    extra={"missing": ["host"]},
                ),
            )
        return errors

    @classmethod
    def needs_oauth2(cls, ex: Exception) -> bool:
        """
        ``UnauthenticatedError`` from the adapter signals an expired/missing
        bearer token — i.e. we need to run the OAuth2 dance.
        """
        return bool(g) and hasattr(g, "user") and isinstance(ex, cls.oauth2_exception)

    @classmethod
    def impersonate_user(
        cls,
        database: Database,  # noqa: ARG003
        username: str | None,  # noqa: ARG003
        user_token: str | None,
        url: URL,
        engine_kwargs: dict[str, Any],
    ) -> tuple[URL, dict[str, Any]]:
        """
        Inject the user's OAuth2 access token into the URL query string so
        that the dialect (and therefore the adapter) sends it as a Bearer.
        """
        if user_token:
            url = url.update_query_dict({"access_token": user_token})
        return url, engine_kwargs

    @classmethod
    def adjust_engine_params(
        cls,
        uri: URL,
        connect_args: dict[str, Any],
        catalog: str | None = None,
        schema: str | None = None,
    ) -> tuple[URL, dict[str, Any]]:
        """
        Fold ``additional_configuration`` and ``http_path`` from the
        database's ``extra`` field (placed by the user under
        ``engine_params.connect_args``) into the URL query string, so the
        dialect can pick them up.

        These are popped from ``connect_args`` *before* calling ``super()``,
        rather than from the dict it returns: the base implementation
        rebuilds ``connect_args`` as a new dict (``{**connect_args, ...}``),
        and Superset's caller never writes that returned dict back into the
        ``extra.engine_params.connect_args`` it passed in, so mutating the
        copy would silently leave the original keys in place for
        ``create_engine``.
        """
        query = dict(uri.query)
        if (config := connect_args.pop("additional_configuration", None)) is not None:
            query["additional_configuration"] = (
                config if isinstance(config, str) else json.dumps(config)
            )
        if (http_path := connect_args.pop("http_path", None)) is not None:
            query["http_path"] = http_path
        if catalog is not None:
            query["pseudo_catalog"] = catalog
        if schema is not None:
            query["pseudo_schema"] = schema
        if query != dict(uri.query):
            uri = uri.set(query=query)

        uri, connect_args = super().adjust_engine_params(
            uri, connect_args, catalog, schema
        )
        return uri, connect_args

    @classmethod
    def get_default_catalog(cls, database: Database) -> str | None:
        return min(database.get_all_catalog_names(), default=None)

    @classmethod
    def get_default_schema(cls, database: Database, catalog: str | None) -> str | None:
        return min(database.get_all_schema_names(catalog=catalog), default=None)

    @classmethod
    def get_catalog_names(cls, database: Database, inspector: Inspector) -> set[str]:
        return set(inspector.dialect.get_catalog_names())

    @classmethod
    def quote_table(cls, table: Table, dialect: Any) -> str:
        return dialect.identifier_preparer.quote(table.table)

    @classmethod
    def select_star(cls, *args: Any, **kwargs: Any) -> str:
        """
        Return a stand-in ``SELECT *`` that explains why preview is disabled.
        """
        message = SELECT_STAR_MESSAGE.replace("'", "''")
        return f"SELECT '{message}' AS warning"

    @classmethod
    def get_columns(
        cls,
        inspector: Inspector,
        table: Table,
        options: dict[str, Any] | None = None,
    ) -> list[ResultSetColumnType]:
        """
        Return only the view's dimensions; metrics are surfaced by ``get_metrics``.
        """
        columns: list[ResultSetColumnType] = []
        for column in inspector.get_columns(table.table, table.schema):
            if "computed" in column:
                continue
            column["column_name"] = column["name"]
            columns.append(column)
        return columns

    @classmethod
    def get_metrics(
        cls,
        database: Database,  # noqa: ARG003
        inspector: Inspector,
        table: Table,
    ) -> list[MetricType]:
        """
        Translate the view's metric columns into Superset metric definitions.
        """
        metrics: list[MetricType] = []
        for column in inspector.get_columns(table.table, table.schema):
            if "computed" not in column:
                continue
            metric: MetricType = {
                "metric_name": column["name"],
                "expression": column["computed"]["sqltext"],
                "description": column["comment"],
            }
            metadata = _semantic_metadata(column)
            if d3format := d3format_from_metadata(metadata):
                metric["d3format"] = d3format
            metrics.append(metric)
        return metrics
