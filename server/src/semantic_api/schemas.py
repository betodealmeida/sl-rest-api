"""Pydantic request models for the REST API.

These mirror :class:`SemanticQuery` and friends with metric and dimension
references collapsed to their stable string ids; the service layer resolves
those ids against the live view.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from superset_core.semantic_layers.types import (
    OrderDirection,
    Operator,
    PredicateType,
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListViewsRequest(_Model):
    runtime_configuration: dict[str, Any] = Field(default_factory=dict)
    path: list[str | None] | None = None


class DiscoverRequest(_Model):
    runtime_configuration: dict[str, Any] = Field(default_factory=dict)
    path: list[str | None] = Field(default_factory=list)


class ViewRequest(_Model):
    """Common base for any endpoint that needs to materialise a view."""

    additional_configuration: dict[str, Any] = Field(default_factory=dict)


class FilterPayload(_Model):
    type: PredicateType = PredicateType.WHERE
    column: str | None
    operator: Operator
    value: Any = None


class OrderPayload(_Model):
    by: str
    direction: OrderDirection = OrderDirection.ASC


class GroupLimitPayload(_Model):
    dimensions: list[str]
    top: int
    metric: str | None = None
    direction: OrderDirection = OrderDirection.DESC
    group_others: bool = False
    filters: list[FilterPayload] = Field(default_factory=list)


class QueryPayload(_Model):
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterPayload] = Field(default_factory=list)
    order: list[OrderPayload] = Field(default_factory=list)
    limit: int | None = None
    offset: int | None = None
    group_limit: GroupLimitPayload | None = None


class QueryRequest(ViewRequest):
    query: QueryPayload


class ValuesRequest(ViewRequest):
    dimension: str
    filters: list[FilterPayload] = Field(default_factory=list)


class CompatibilityRequest(ViewRequest):
    selected_metrics: list[str] = Field(default_factory=list)
    selected_dimensions: list[str] = Field(default_factory=list)
