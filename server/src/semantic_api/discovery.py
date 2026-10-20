"""The reference server's view discovery hierarchy."""

from __future__ import annotations

from typing import Any, Iterable

from litestar.exceptions import NotFoundException, ValidationException
from superset_core.semantic_layers.view import SemanticView

# The final level is absent for marketing. Its JSON discovery value is null,
# which remains distinct from a string that happens to be named "null".
_LOCATIONS: dict[str, tuple[str | None, ...]] = {
    "marketing": ("foo", "bar", None),
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
    views: Iterable[SemanticView], path: list[str | None], *, discovering: bool
) -> list[tuple[SemanticView, tuple[str | None, ...]]]:
    max_depth = len(_LEVELS) - 1 if discovering else len(_LEVELS)
    if len(path) > max_depth:
        raise ValidationException(detail="Path extends beyond the discovery hierarchy.")
    matches = _matches(views, path)
    if path and not matches:
        raise NotFoundException(detail="Unknown discovery path.")
    return matches


def discover(
    views: Iterable[SemanticView], path: list[str | None]
) -> dict[str, Any]:
    """Return the values immediately below a selected location."""
    matches = _selected(views, path, discovering=True)
    depth = len(path)
    values = sorted(
        {location[depth] for _, location in matches},
        key=lambda value: (value is None, value or ""),
    )
    result: dict[str, Any] = {"level": _LEVELS[depth], "values": values}
    if depth + 1 < len(_LEVELS):
        result["next_level"] = _LEVELS[depth + 1]
    return result


def filter_views(
    views: list[SemanticView], path: list[str | None] | None
) -> list[SemanticView]:
    """Filter view summaries by a positional discovery path."""
    if path is None or not path:
        return views
    return [view for view, _ in _selected(views, path, discovering=False)]
