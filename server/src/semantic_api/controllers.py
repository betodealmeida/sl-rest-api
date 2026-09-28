"""HTTP route handlers — everything hangs off ``/views``."""

from __future__ import annotations

from typing import Any

from litestar import Controller, post
from litestar.exceptions import NotFoundException
from litestar.status_codes import HTTP_200_OK

from semantic_api import registry
from semantic_api.auth import require_bearer
from semantic_api.schemas import (
    CompatibilityRequest,
    ListViewsRequest,
    QueryPayload,
    QueryRequest,
    ValuesRequest,
    ViewRequest,
)
from semantic_api.serialization import (
    dimension_to_payload,
    metric_to_payload,
    result_to_payload,
)
from semantic_api.service import build_query, fetch_view


def _view_summary_payload(view) -> dict[str, Any]:  # noqa: ANN001 - SemanticView
    payload = {
        "name": view.name,
        "uid": view.uid(),
        "features": sorted(f.value for f in view.features),
    }
    display_name = getattr(view, "display_name", None)
    if isinstance(display_name, str) and display_name:
        payload["display_name"] = display_name
    return payload


def _view_payload(view) -> dict[str, Any]:  # noqa: ANN001 - SemanticView
    return {
        **_view_summary_payload(view),
        "dimensions": [dimension_to_payload(d) for d in view.get_dimensions()],
        "metrics": [metric_to_payload(m) for m in view.get_metrics()],
    }


class ViewsController(Controller):
    path = "/views"
    tags = ["views"]
    guards = [require_bearer]

    @post(["/list", "/"], status_code=HTTP_200_OK)
    async def list_views(self, data: ListViewsRequest) -> list[dict[str, Any]]:
        views = registry.layer().get_semantic_views(data.runtime_configuration)
        return [_view_summary_payload(view) for view in views]

    @post("/{view_name:str}", status_code=HTTP_200_OK)
    async def get_view(self, view_name: str, data: ViewRequest) -> dict[str, Any]:
        view = fetch_view(view_name, data.additional_configuration)
        return _view_payload(view)

    @post("/{view_name:str}/query", status_code=HTTP_200_OK)
    async def query(self, view_name: str, data: QueryRequest) -> dict[str, Any]:
        view = fetch_view(view_name, data.additional_configuration)
        return result_to_payload(view.get_table(build_query(view, data.query)))

    @post("/{view_name:str}/row-count", status_code=HTTP_200_OK)
    async def row_count(self, view_name: str, data: QueryRequest) -> dict[str, Any]:
        view = fetch_view(view_name, data.additional_configuration)
        return result_to_payload(view.get_row_count(build_query(view, data.query)))

    @post("/{view_name:str}/values", status_code=HTTP_200_OK)
    async def values(self, view_name: str, data: ValuesRequest) -> dict[str, Any]:
        view = fetch_view(view_name, data.additional_configuration)
        dimension = next(
            (d for d in view.get_dimensions() if d.id == data.dimension),
            None,
        )
        if dimension is None:
            raise NotFoundException(detail=f"Unknown dimension {data.dimension!r}.")
        filters = build_query(view, QueryPayload(filters=data.filters)).filters
        return result_to_payload(view.get_values(dimension, filters))

    @post("/{view_name:str}/compatible-metrics", status_code=HTTP_200_OK)
    async def compatible_metrics(
        self,
        view_name: str,
        data: CompatibilityRequest,
    ) -> list[dict[str, Any]]:
        view = fetch_view(view_name, data.additional_configuration)
        selected_metrics, selected_dimensions = _resolve_selection(view, data)
        metrics = view.get_compatible_metrics(selected_metrics, selected_dimensions)
        return [metric_to_payload(m) for m in metrics]

    @post("/{view_name:str}/compatible-dimensions", status_code=HTTP_200_OK)
    async def compatible_dimensions(
        self,
        view_name: str,
        data: CompatibilityRequest,
    ) -> list[dict[str, Any]]:
        view = fetch_view(view_name, data.additional_configuration)
        selected_metrics, selected_dimensions = _resolve_selection(view, data)
        dimensions = view.get_compatible_dimensions(selected_metrics, selected_dimensions)
        return [dimension_to_payload(d) for d in dimensions]


def _resolve_selection(view, data: CompatibilityRequest):
    metrics_by_id = {m.id: m for m in view.get_metrics()}
    dimensions_by_id = {d.id: d for d in view.get_dimensions()}
    return (
        {metrics_by_id[m] for m in data.selected_metrics if m in metrics_by_id},
        {dimensions_by_id[d] for d in data.selected_dimensions if d in dimensions_by_id},
    )
