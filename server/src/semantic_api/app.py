"""Litestar application factory."""

from __future__ import annotations

import json
import os
from typing import Any

from litestar import Litestar
from litestar.openapi import OpenAPIConfig
from litestar.plugins.problem_details import ProblemDetailsPlugin

from semantic_api import registry
from semantic_api.auth import AuthController
from semantic_api.controllers import ViewsController


def _read_configuration() -> dict[str, Any]:
    raw = os.environ.get("SEMANTIC_LAYER_CONFIGURATION")
    return json.loads(raw) if raw else {}


def create_app() -> Litestar:
    registry.configure(_read_configuration())

    return Litestar(
        route_handlers=[AuthController, ViewsController],
        openapi_config=OpenAPIConfig(
            title="Semantic Layer API",
            version="0.1.0",
            description="REST interface for a Superset semantic layer.",
        ),
        plugins=[ProblemDetailsPlugin()],
        logging_config=None,
    )


app = create_app()
