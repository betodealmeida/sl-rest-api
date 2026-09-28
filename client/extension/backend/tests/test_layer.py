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

from netflix.quiver_semantic_layer.configuration import QuiverConfiguration
from netflix.quiver_semantic_layer.layer import QuiverSemanticLayer


def configuration() -> QuiverConfiguration:
    return QuiverConfiguration(
        api_url="https://quiver.example.test/api",
        client_id="client-id",
        client_secret="client-secret",
    )


def test_configuration_schema_marks_client_secret_write_only() -> None:
    schema = QuiverSemanticLayer.get_configuration_schema()

    assert schema["properties"]["client_secret"]["writeOnly"] is True


def test_configuration_schema_uses_plain_optional_url_fields() -> None:
    properties = QuiverSemanticLayer.get_configuration_schema()["properties"]

    assert properties["authorization_url"]["type"] == "string"
    assert properties["authorization_url"]["default"] == ""
    assert properties["token_url"]["type"] == "string"
    assert properties["token_url"]["default"] == ""


def test_configuration_schema_omits_unused_mapping_fields() -> None:
    properties = QuiverSemanticLayer.get_configuration_schema()["properties"]

    assert "additional_configuration" not in properties
    assert "authorization_params" not in properties
    assert "token_params" not in properties


def test_configuration_action_reflects_personal_token(monkeypatch) -> None:
    monkeypatch.setattr(
        "netflix.quiver_semantic_layer.layer.has_usable_credentials",
        lambda config: True,
    )

    action = QuiverSemanticLayer.get_configuration_actions(configuration())[0]

    assert action.id == "oauth2"
    assert action.required is True
    assert action.status == "connected"
