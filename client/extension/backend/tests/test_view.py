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

from __future__ import annotations

from datetime import date
from typing import Any

from superset_core.semantic_layers.types import SemanticQuery

from betodealmeida.semantic_layer_api.view import APISemanticView, _semantic_result


class FakeClient:
    def __init__(self) -> None:
        self.query_payload: dict[str, Any] | None = None

    def get_view(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "uid": f"api.{name}",
            "features": ["GROUP_LIMIT"],
            "dimensions": [
                {
                    "id": "sales.day",
                    "name": "day",
                    "type": "date",
                    "definition": "day",
                    "description": "Sales day",
                    "grain": {"name": "Day", "representation": "P1D"},
                    "metadata": {"display_name": "Sales day"},
                }
            ],
            "metrics": [
                {
                    "id": "sales.revenue",
                    "name": "revenue",
                    "type": "floating",
                    "definition": "SUM(revenue)",
                    "description": "Revenue",
                    "aggregation": "SUM",
                    "metadata": {
                        "display_name": "Revenue",
                        "extensions": {"superset": {"d3format": "$,.2f"}},
                    },
                }
            ],
        }

    def query(self, name: str, query: dict[str, Any]) -> dict[str, Any]:
        self.query_payload = query
        return {
            "requests": [{"type": "api", "definition": "request-id"}],
            "results": {
                "schema": [
                    {"name": "day", "type": "date"},
                    {"name": "revenue", "type": "floating"},
                ],
                "rows": [{"day": "2026-09-28", "revenue": 42.5}],
            },
        }


def test_metadata_and_query_results_are_adapted() -> None:
    client = FakeClient()
    view = APISemanticView("sales", client)  # type: ignore[arg-type]
    dimension = next(iter(view.get_dimensions()))
    metric = next(iter(view.get_metrics()))

    assert view.uid() == "api.sales"
    assert dimension.verbose_name == "Sales day"
    assert metric.verbose_name == "Revenue"
    assert metric.d3format == "$,.2f"

    result = view.get_table(
        SemanticQuery(metrics=[metric], dimensions=[dimension], limit=10)
    )

    assert client.query_payload == {
        "metrics": ["sales.revenue"],
        "dimensions": ["sales.day"],
        "filters": [],
        "order": [],
        "limit": 10,
        "offset": None,
    }
    assert result.requests[0].definition == "request-id"
    assert result.results.to_pylist() == [{"day": date(2026, 9, 28), "revenue": 42.5}]


def test_query_results_accept_canonical_arrow_type_names() -> None:
    result = _semantic_result(
        {
            "results": {
                "schema": [
                    {"name": "count", "type": "int64"},
                    {"name": "label", "type": "large_utf8"},
                ],
                "rows": [{"count": 42, "label": 7}],
            }
        }
    )

    assert result.results.schema.field("count").type == "int64"
    assert result.results.to_pylist() == [{"count": 42, "label": "7"}]
