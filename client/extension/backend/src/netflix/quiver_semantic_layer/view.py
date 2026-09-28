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

"""Semantic-view adapter for the Quiver REST protocol."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import pyarrow as pa
from superset_core.semantic_layers.types import (
    AggregationType,
    Dimension,
    Filter,
    Grain,
    Grains,
    Metric,
    SemanticQuery,
    SemanticRequest,
    SemanticResult,
)
from superset_core.semantic_layers.view import SemanticView, SemanticViewFeature

from .client import QuiverClient

_TYPE_MAP: dict[str, pa.DataType] = {
    "bool": pa.bool_(),
    "boolean": pa.bool_(),
    "int": pa.int64(),
    "int8": pa.int8(),
    "int16": pa.int16(),
    "int32": pa.int32(),
    "int64": pa.int64(),
    "integer": pa.int64(),
    "uint8": pa.uint8(),
    "uint16": pa.uint16(),
    "uint32": pa.uint32(),
    "uint64": pa.uint64(),
    "halffloat": pa.float16(),
    "floating": pa.float64(),
    "float": pa.float64(),
    "float16": pa.float16(),
    "float32": pa.float32(),
    "float64": pa.float64(),
    "double": pa.float64(),
    "utf8": pa.string(),
    "string": pa.string(),
    "large_string": pa.large_string(),
    "large_utf8": pa.large_string(),
    "binary": pa.binary(),
    "large_binary": pa.large_binary(),
    "date": pa.date32(),
    "date32": pa.date32(),
    "date64": pa.date64(),
    "time": pa.time64("us"),
    "time32": pa.time32("s"),
    "time64": pa.time64("us"),
    "timestamp": pa.timestamp("us"),
    "decimal": pa.decimal128(38, 9),
    "duration": pa.duration("us"),
    "null": pa.null(),
}


class QuiverSemanticView(SemanticView):
    """A semantic view whose operations are forwarded to Quiver."""

    def __init__(
        self,
        name: str,
        client: QuiverClient,
        summary: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.display_name = (
            str(summary["display_name"])
            if summary and summary.get("display_name")
            else name
        )
        self._client = client
        self._payload: dict[str, Any] | None = None
        self._uid = str(summary.get("uid", name)) if summary else name
        self.features = (
            _features(summary.get("features", [])) if summary else frozenset()
        )
        self._dimensions: set[Dimension] | None = None
        self._metrics: set[Metric] | None = None

    def uid(self) -> str:
        return self._uid

    def get_dimensions(self) -> set[Dimension]:
        self._load_metadata()
        return set(self._dimensions or set())

    def get_metrics(self) -> set[Metric]:
        self._load_metadata()
        return set(self._metrics or set())

    def get_values(
        self,
        dimension: Dimension,
        filters: set[Filter] | None = None,
    ) -> SemanticResult:
        payload = self._client.values(
            self.name,
            dimension.id,
            [_filter_payload(item) for item in filters or set()],
        )
        return _semantic_result(payload)

    def get_table(self, query: SemanticQuery) -> SemanticResult:
        return _semantic_result(self._client.query(self.name, _query_payload(query)))

    def get_row_count(self, query: SemanticQuery) -> SemanticResult:
        return _semantic_result(
            self._client.row_count(self.name, _query_payload(query))
        )

    def get_compatible_metrics(
        self,
        selected_metrics: set[Metric],
        selected_dimensions: set[Dimension],
    ) -> set[Metric]:
        metrics = {metric.id: metric for metric in self.get_metrics()}
        payloads = self._client.compatible(
            self.name,
            "metrics",
            [metric.id for metric in selected_metrics],
            [dimension.id for dimension in selected_dimensions],
        )
        return {metrics[item["id"]] for item in payloads if item.get("id") in metrics}

    def get_compatible_dimensions(
        self,
        selected_metrics: set[Metric],
        selected_dimensions: set[Dimension],
    ) -> set[Dimension]:
        dimensions = {dimension.id: dimension for dimension in self.get_dimensions()}
        payloads = self._client.compatible(
            self.name,
            "dimensions",
            [metric.id for metric in selected_metrics],
            [dimension.id for dimension in selected_dimensions],
        )
        return {
            dimensions[item["id"]] for item in payloads if item.get("id") in dimensions
        }

    def _load_metadata(self) -> None:
        if self._payload is not None:
            return
        self._payload = self._client.get_view(self.name)
        self._uid = str(self._payload.get("uid", self._uid))
        if self._payload.get("display_name"):
            self.display_name = str(self._payload["display_name"])
        self.features = _features(self._payload.get("features", []))
        self._dimensions = {
            _dimension(item)
            for item in self._payload.get("dimensions", [])
            if isinstance(item, Mapping)
        }
        self._metrics = {
            _metric(item)
            for item in self._payload.get("metrics", [])
            if isinstance(item, Mapping)
        }


def _features(values: Any) -> frozenset[SemanticViewFeature]:
    known = {feature.value: feature for feature in SemanticViewFeature}
    return frozenset(known[value] for value in values if value in known)


def _dimension(payload: Mapping[str, Any]) -> Dimension:
    metadata, display_name = _metadata(payload)
    grain_payload = payload.get("grain")
    grain: Grain | None = None
    if isinstance(grain_payload, Mapping) and grain_payload.get("representation"):
        grain = Grains.get(
            str(grain_payload["representation"]),
            str(grain_payload.get("name") or grain_payload["representation"]),
        )
    return Dimension(
        id=str(payload["id"]),
        name=str(payload["name"]),
        type=_arrow_type(str(payload.get("type", "utf8"))),
        definition=(
            str(payload["definition"])
            if payload.get("definition") is not None
            else None
        ),
        description=(
            str(payload["description"])
            if payload.get("description") is not None
            else None
        ),
        grain=grain,
        verbose_name=display_name,
        metadata=metadata,
    )


def _metric(payload: Mapping[str, Any]) -> Metric:
    metadata, display_name = _metadata(payload)
    aggregation_value = payload.get("aggregation")
    try:
        aggregation = (
            AggregationType(str(aggregation_value))
            if aggregation_value is not None
            else None
        )
    except ValueError:
        aggregation = AggregationType.OTHER
    return Metric(
        id=str(payload["id"]),
        name=str(payload["name"]),
        type=_arrow_type(str(payload.get("type", "floating"))),
        definition=str(payload.get("definition") or payload["name"]),
        description=(
            str(payload["description"])
            if payload.get("description") is not None
            else None
        ),
        aggregation=aggregation,
        verbose_name=display_name,
        d3format=_d3format(metadata),
        metadata=metadata,
    )


def _metadata(payload: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    raw = payload.get("metadata")
    metadata = deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
    display_name = metadata.pop("display_name", None)
    return metadata, str(display_name) if display_name is not None else None


def _d3format(metadata: Mapping[str, Any]) -> str | None:
    extensions = metadata.get("extensions")
    if isinstance(extensions, Mapping):
        superset = extensions.get("superset")
        if isinstance(superset, Mapping) and isinstance(superset.get("d3format"), str):
            return str(superset["d3format"])
    return None


def _arrow_type(value: str) -> pa.DataType:
    return _TYPE_MAP.get(value.split("[", maxsplit=1)[0].lower(), pa.string())


def _query_payload(query: SemanticQuery) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "metrics": [metric.id for metric in query.metrics],
        "dimensions": [dimension.id for dimension in query.dimensions],
        "filters": [_filter_payload(item) for item in query.filters or set()],
        "order": [
            {"by": element.id, "direction": direction.value}
            for element, direction in query.order or []
        ],
        "limit": query.limit,
        "offset": query.offset,
    }
    if query.group_limit is not None:
        payload["group_limit"] = {
            "dimensions": [item.id for item in query.group_limit.dimensions],
            "top": query.group_limit.top,
            "metric": query.group_limit.metric.id if query.group_limit.metric else None,
            "direction": query.group_limit.direction.value,
            "group_others": query.group_limit.group_others,
            "filters": [
                _filter_payload(item) for item in query.group_limit.filters or set()
            ],
        }
    return payload


def _filter_payload(filter_: Filter) -> dict[str, Any]:
    return {
        "type": filter_.type.value,
        "column": filter_.column.id if filter_.column else None,
        "operator": filter_.operator.value,
        "value": filter_.value,
    }


def _semantic_result(payload: Mapping[str, Any]) -> SemanticResult:
    requests_payload = payload.get("requests", [])
    requests_ = [
        SemanticRequest(
            type=str(item.get("type", "quiver")),
            definition=str(item.get("definition", "")),
        )
        for item in requests_payload
        if isinstance(item, Mapping)
    ]
    results = payload.get("results")
    if not isinstance(results, Mapping):
        return SemanticResult(requests=requests_, results=pa.table({}))
    schema = results.get("schema", [])
    rows = results.get("rows", [])
    columns: dict[str, pa.Array] = {}
    for field in schema:
        if not isinstance(field, Mapping) or "name" not in field:
            continue
        name = str(field["name"])
        type_name = str(field.get("type", "utf8"))
        arrow_type = _arrow_type(type_name)
        values = [
            _coerce_value(row.get(name), arrow_type)
            for row in rows
            if isinstance(row, Mapping)
        ]
        try:
            columns[name] = pa.array(values, type=arrow_type)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError) as ex:
            value_types = sorted(
                {type(value).__name__ for value in values if value is not None}
            )
            raise ValueError(
                f"Could not convert Quiver column {name!r} with declared type "
                f"{type_name!r} to Arrow {arrow_type}; received value types "
                f"{value_types or ['null']}"
            ) from ex
    return SemanticResult(requests=requests_, results=pa.table(columns))


def _coerce_value(value: Any, arrow_type: pa.DataType) -> Any:
    if value is None:
        return value

    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return value if isinstance(value, str) else str(value)
    if pa.types.is_binary(arrow_type) or pa.types.is_large_binary(arrow_type):
        return value.encode() if isinstance(value, str) else value
    if not isinstance(value, str):
        return value
    if pa.types.is_date(arrow_type):
        return date.fromisoformat(value)
    if pa.types.is_time(arrow_type):
        return time.fromisoformat(value)
    if pa.types.is_timestamp(arrow_type):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if pa.types.is_decimal(arrow_type):
        return Decimal(value)
    if pa.types.is_integer(arrow_type):
        return int(value)
    if pa.types.is_floating(arrow_type):
        return float(value)
    if pa.types.is_boolean(arrow_type):
        normalized = value.casefold()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return value
