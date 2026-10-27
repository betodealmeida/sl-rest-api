"""The reference server's view discovery hierarchy."""

from __future__ import annotations

from typing import Any, Iterable

from litestar.exceptions import NotFoundException, ValidationException
from superset_core.semantic_layers.view import SemanticView

_LOCATIONS: dict[str, tuple[str | None, ...]] = {
    "marketing": ("foo", "bar"),
    "sales": ("foo", "bar", "baz"),
}
_LEVELS = (
    {"id": "group", "display_name": "Group"},
    {"id": "area", "display_name": "Area"},
    {"id": "variant", "display_name": "Variant"},
)


def _matches(
    views: Iterable[SemanticView], path: list[str | None]
) -> list[tuple[SemanticView, tuple[str | None, ...]]]:
    selected = tuple(path)
    return [
        (view, location)
        for view in views
        if (location := _LOCATIONS.get(view.name)) is not None
        and location[: len(selected)] == selected
    ]


def _selected(
    views: Iterable[SemanticView], path: list[str | None]
) -> list[tuple[SemanticView, tuple[str | None, ...]]]:
    if len(path) > len(_LEVELS):
        raise ValidationException(detail="Path extends beyond the discovery hierarchy.")
    matches = _matches(views, path)
    if path and not matches:
        raise NotFoundException(detail="Unknown discovery path.")
    return matches


def browse(
    views: Iterable[SemanticView], path: list[str | None]
) -> tuple[dict[str, Any], list[SemanticView]]:
    """Return child choices and views at a selected location."""
    matches = _selected(views, path)
    depth = len(path)
    values = sorted(
        {location[depth] for _, location in matches if len(location) > depth},
        key=lambda value: (value is None, value or ""),
    )
    hierarchy: dict[str, Any] = {"next": _LEVELS[depth] if depth < len(_LEVELS) else None}
    if hierarchy["next"] is not None:
        hierarchy["values"] = values
    return hierarchy, [view for view, location in matches if len(location) == depth]
