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

# pylint: disable=unused-argument, abstract-method

"""
A SQLAlchemy dialect for the Semantic Layer REST API.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, ClassVar, cast
from urllib.parse import quote, unquote

import requests
import sqlalchemy.types
from sqlalchemy.engine.base import Connection
from sqlalchemy.engine.url import URL
from sqlalchemy.sql.type_api import TypeEngine

from shillelagh.adapters.registry import registry
from shillelagh.backends.apsw.dialects.base import (
    APSWDialect,
    SQLAlchemyColumn,
    get_adapter_for_table_name,
)
from shillelagh.exceptions import UnauthenticatedError
from shillelagh.fields import Field

from sl_db_engine_spec.adapter import SemanticAPI

_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_TIMEOUT = 60.0


def encode_namespace_label(value: str) -> str:
    """Encode a catalog or schema for Superset's dot-delimited permissions."""
    # urllib.quote always leaves ASCII dots unescaped, even with safe="".
    # Encode them explicitly so a multi-part Git root remains one component.
    return quote(value, safe="").replace(".", "%2E")


def encode_schema_label(value: str | None) -> str:
    """Give an absent level a distinct Superset label from every string value."""
    if value is None:
        return "unversioned"
    label = encode_namespace_label(value)
    return f"~{label}" if label == "unversioned" or label.startswith("~") else label


def decode_schema_label(label: str) -> str | None:
    """Recover the JSON discovery value from its Superset schema label."""
    if label == "unversioned":
        return None
    return unquote(label[1:] if label.startswith("~") else label)


def get_sqla_type(field: Field) -> type[TypeEngine]:
    """
    Convert from Shillelagh to SQLAlchemy types.
    """
    type_map: dict[str, type[TypeEngine]] = {
        "BOOLEAN": sqlalchemy.types.BOOLEAN,
        "INTEGER": sqlalchemy.types.INT,
        "REAL": sqlalchemy.types.FLOAT,
        "DECIMAL": sqlalchemy.types.DECIMAL,
        "TIMESTAMP": sqlalchemy.types.TIMESTAMP,
        "DATE": sqlalchemy.types.DATE,
        "TIME": sqlalchemy.types.TIME,
        "TEXT": sqlalchemy.types.TEXT,
    }

    return type_map.get(field.type, sqlalchemy.types.TEXT)


class TableSemanticAPI(SemanticAPI):
    """
    Base class for per-server SemanticAPI adapter subclasses.

    Each server, configuration, token, catalog, and schema selection gets its
    own dynamically generated subclass — see :func:`adapter_class` — so the
    set of accessible table names is isolated per connection scope. The base
    class is never registered directly.
    """

    table_names: ClassVar[set[str]] = set()
    server_url: ClassVar[str] = ""
    server_configuration: ClassVar[dict[str, Any]] = {}
    server_access_token: ClassVar[str | None] = None

    @classmethod
    def supports(cls, uri: str, fast: bool = True, **kwargs: Any) -> bool:
        return uri in cls.table_names

    @staticmethod
    def parse_uri(uri: str) -> tuple[str]:
        # The dialect feeds ``uri`` as a bare table name (e.g. ``"sales"``);
        # the base class' parser would try to interpret it as a full
        # ``semantic-api+http://...`` URI and reject it.
        return (uri,)

    def __init__(self, table: str, request_timeout: float = _DEFAULT_TIMEOUT) -> None:
        view_url = f"{type(self).server_url.rstrip('/')}/views/{table}"
        super().__init__(
            view_url,
            table,
            type(self).server_access_token,
            type(self).server_configuration,
            request_timeout,
        )


_ADAPTER_CACHE: dict[str, type[TableSemanticAPI]] = {}


def adapter_class(
    base_url: str,
    configuration: dict[str, Any],
    access_token: str | None = None,
    catalog: str | None = None,
    schema: str | None = None,
) -> tuple[str, type[TableSemanticAPI]]:
    """
    Return an adapter class scoped to one connection's view namespace.

    Subclasses are cached so repeat connections from the same tenant reuse a
    single class — different tenants get their own. The access token is part
    of the key so that two tenants pointing at the same server with different
    bearer tokens, catalogs, or schemas can't see each other's ``table_names``.
    """
    fingerprint = json.dumps(
        [base_url, configuration, access_token, catalog, schema],
        sort_keys=True,
    ).encode()
    digest = hashlib.blake2b(fingerprint, digest_size=8).hexdigest()
    name = f"semanticapi_{digest}"
    if name in _ADAPTER_CACHE:
        return name, _ADAPTER_CACHE[name]

    cls = cast(
        type[TableSemanticAPI],
        type(
            f"TableSemanticAPI_{digest}",
            (TableSemanticAPI,),
            {
                "table_names": set(),
                "server_url": base_url,
                "server_configuration": dict(configuration),
                "server_access_token": access_token,
            },
        ),
    )
    _ADAPTER_CACHE[name] = cls
    return name, cls


class SemanticAPIDialect(APSWDialect):
    """
    A Semantic Layer REST API dialect.

    URL should look like::

        semanticapi://host[:port]/

    Query parameters:
        ``encryption``                set to ``true`` to use HTTPS
        ``http_path``                 base path where the REST API is
                                      mounted on the server (e.g.
                                      ``/api/datajunction/semantic``)
        ``additional_configuration``  JSON object forwarded to the view
                                      (used as ``runtime_configuration``
                                      when listing views)

    Each semantic view on the server is exposed as a SQL table named after
    the view itself.
    """

    name = "semanticapi"

    supports_statement_cache = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url: str = ""
        self._configuration: dict[str, Any] = {}
        self._access_token: str | None = None
        self._adapter_name: str = ""
        self._adapter_cls: type[TableSemanticAPI] | None = None
        self._catalog: str | None = None
        self._schema: str | None = None
        self._views: list[str] = []
        self._view_locations: dict[str, tuple[str, str]] = {}

    def create_connect_args(self, url: URL) -> tuple[tuple[()], dict[str, Any]]:
        encryption = str(url.query.get("encryption", "")).lower() in _TRUTHY
        scheme = "https" if encryption else "http"
        netloc = f"{url.host}:{url.port}" if url.port else url.host
        http_path = str(url.query.get("http_path", "")).strip("/")
        self._base_url = f"{scheme}://{netloc}/{http_path}".rstrip("/")

        self._configuration = {}
        if raw := url.query.get("additional_configuration"):
            self._configuration = json.loads(raw)

        self._access_token = url.query.get("access_token") or None
        self._catalog = url.query.get("pseudo_catalog") or None
        self._schema = url.query.get("pseudo_schema") or None

        self._adapter_name, self._adapter_cls = adapter_class(
            self._base_url,
            self._configuration,
            self._access_token,
            self._catalog,
            self._schema,
        )
        # populate the known-table set so ``supports`` works on first SQL call
        self._refresh_views()
        if self._adapter_name not in registry.loaders:
            registry.add(self._adapter_name, self._adapter_cls)

        return (
            (),
            {
                "path": ":memory:",
                "adapters": [self._adapter_name],
                "adapter_kwargs": {self._adapter_name: {}},
                "safe": True,
                "isolation_level": self.isolation_level,
            },
        )

    def _post(self, endpoint: str, body: dict[str, Any]) -> requests.Response:
        headers: dict[str, str] = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        response = requests.post(
            f"{self._base_url}{endpoint}",
            json=body,
            headers=headers,
            timeout=_DEFAULT_TIMEOUT,
        )
        return response

    @staticmethod
    def _check_response(response: requests.Response) -> None:
        # Surface as the canonical shillelagh exception so the Superset engine
        # spec's ``needs_oauth2`` recognises it and starts the dance. Some
        # servers return a 307 redirect for unauthenticated requests.
        if response.status_code in (307, 401):
            detail = "Authentication required."
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            raise UnauthenticatedError(detail)
        response.raise_for_status()

    def _discover(self, path: list[str | None]) -> dict[str, Any]:
        response = self._post(
            "/discover",
            {"runtime_configuration": self._configuration, "path": path},
        )
        self._check_response(response)
        result = response.json()
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("values"), list)
            or any(
                value is not None and (not isinstance(value, str) or not value)
                for value in result["values"]
            )
            or (not path and None in result["values"])
        ):
            raise ValueError("Invalid semantic view discovery response")
        next_level = result.get("next_level")
        if path and "next_level" in result:
            raise ValueError("The Semantic Layer API must expose exactly two levels")
        if not path and (
            not isinstance(next_level, dict)
            or not isinstance(next_level.get("id"), str)
            or not next_level["id"]
            or not isinstance(next_level.get("display_name"), str)
            or not next_level["display_name"]
        ):
            raise ValueError("The Semantic Layer API must expose exactly two levels")
        return result

    def _list_views(
        self,
        path: list[str | None],
    ) -> tuple[list[str], dict[str, tuple[str, str]]]:
        response = self._post(
            "/views/", {"runtime_configuration": self._configuration, "path": path}
        )
        self._check_response(response)
        entries = response.json()
        names = sorted(view["name"] for view in entries)
        locations: dict[str, tuple[str, str]] = {}
        if len(path) == 2:
            location = (
                encode_namespace_label(cast(str, path[0])),
                encode_schema_label(path[1]),
            )
            locations = {name: location for name in names}
        return names, locations

    def _refresh_views(self) -> None:
        path: list[str | None] = []
        if self._catalog:
            path.append(unquote(self._catalog))
            if self._schema:
                path.append(decode_schema_label(self._schema))
        self._views, self._view_locations = self._list_views(path)
        if self._adapter_cls is not None:
            self._adapter_cls.table_names = set(self._views)

    def get_catalog_names(self, connection: Connection | None = None) -> list[str]:
        return [encode_namespace_label(value) for value in self._discover([])["values"]]

    def get_table_names(
        self,
        connection: Connection,
        schema: str | None = None,
        sqlite_include_internal: bool = False,
        **kwargs: Any,
    ) -> list[str]:
        if self._catalog and schema:
            if self._schema and schema != self._schema:
                return []
            path = [unquote(self._catalog), decode_schema_label(schema)]
            self._views, self._view_locations = self._list_views(path)
            if self._adapter_cls is not None:
                self._adapter_cls.table_names.update(self._views)
            return self._views
        if schema is not None:
            return []
        self._refresh_views()
        return self._views

    def get_view_names(
        self,
        connection: Connection,
        schema: str | None = None,
        **kwargs: Any,
    ) -> list[str]:
        # Semantic views are exposed as virtual tables. SQLite's inherited
        # implementation queries <schema>.sqlite_master, but pseudo-schemas
        # are not attached SQLite databases.
        return []

    def has_table(
        self,
        connection: Connection,
        table_name: str,
        schema: str | None = None,
        **kwargs: Any,
    ) -> bool:
        if schema is not None and self._catalog:
            if self._schema and schema != self._schema:
                return False
            location = self._view_locations.get(table_name)
            if location is None or location[1] != schema:
                self.get_table_names(connection, schema=schema)
                location = self._view_locations.get(table_name)
            return location is not None and location[1] == schema
        return (
            schema is None
            and self._adapter_cls is not None
            and table_name in self._adapter_cls.table_names
        )

    def get_columns(
        self,
        connection: Connection,
        table_name: str,
        schema: str | None = None,
        **kwargs: Any,
    ) -> list[SQLAlchemyColumn]:
        adapter = cast(
            SemanticAPI,
            get_adapter_for_table_name(connection, table_name),
        )
        metric_names = set(adapter.metric_ids)
        column_metadata = adapter.get_column_metadata()

        columns: list[SQLAlchemyColumn] = []
        for name, field in adapter.get_columns().items():
            column: SQLAlchemyColumn = {
                "name": name,
                "type": get_sqla_type(field),
                "nullable": True,
                "default": None,
                "comment": "metric" if name in metric_names else "dimension",
            }
            if name in metric_names:
                column["computed"] = {"sqltext": name, "persisted": True}
            if metadata := column_metadata.get(name):
                column["semantic_metadata"] = metadata
            columns.append(column)
        return columns

    def get_schema_names(
        self,
        connection: Connection,
        **kwargs: Any,
    ) -> list[str]:
        if not self._catalog:
            return []
        result = self._discover([unquote(self._catalog)])
        return [encode_schema_label(value) for value in result["values"]]

    def get_pk_constraint(
        self,
        connection: Connection,
        table_name: str,
        schema: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {"constrained_columns": [], "name": None}

    def get_foreign_keys(
        self,
        connection: Connection,
        table_name: str,
        schema: str | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return []

    get_check_constraints = get_foreign_keys
    get_indexes = get_foreign_keys
    get_unique_constraints = get_foreign_keys

    def get_table_comment(self, connection, table_name, schema=None, **kwargs):
        return {"text": "A semantic view exposed as a virtual table."}


class SemanticAPIHttpsDialect(SemanticAPIDialect):
    """
    Identical to :class:`SemanticAPIDialect` but defaults to HTTPS.

    Registered as ``semanticapi+https://``, so encryption is on unless the
    caller explicitly passes ``?encryption=false``.
    """

    def create_connect_args(self, url: URL) -> tuple[tuple[()], dict[str, Any]]:
        if "encryption" not in url.query:
            url = url.update_query_dict({"encryption": "true"})
        return super().create_connect_args(url)
