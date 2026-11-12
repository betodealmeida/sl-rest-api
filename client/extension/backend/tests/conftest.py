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

from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

import pytest
from superset_core.rest_api import decorators as rest_api_decorators
from superset_core.semantic_layers import decorators
from superset_core.semantic_layers.context import (
    ExecutionOrigin,
    SemanticExecutionContext,
)
from superset_core.semantic_layers.transport import ProviderTransport


def _semantic_layer(**_: Any) -> Callable[[type], type]:
    def register(cls: type) -> type:
        return cls

    return register


decorators.semantic_layer = _semantic_layer

rest_api_decorators.api = _semantic_layer


@pytest.fixture
def transport():
    return Mock(spec=ProviderTransport)


@pytest.fixture
def binding(transport):
    return {
        "transport": transport,
        "cache_token": "host-token",
        "context": SemanticExecutionContext(
            principal_id="1",
            role_ids=("2",),
            origin=ExecutionOrigin.REQUEST,
            is_guest=False,
            namespace="test",
            identity_digest="opaque-identity",
        ),
    }
