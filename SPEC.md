# Semantic Layer REST API

This specification describes a simple REST API for accessing a semantic layer and associated semantic views (models/cubes/etc., depending on the terminology used). It was built in order to integrate [Apache Superset](https://superset.apache.org/) with [DataJunction](https://datajunction.io/), but is generic enough to be implemented in other semantic layers and accessed by other clients.

Because it was built with Superset as its first client, it maps the Python interface 1:1 with the REST calls:

## Concepts

| Python                  | REST resource                               |
| ----------------------- | ------------------------------------------- |
| `SemanticView`          | `/views/{view_name}`                        |
| `Dimension`, `Metric`   | Embedded objects, keyed by stable `id`      |
| `SemanticQuery`         | Request body of `/views/{view_name}/query`  |
| `SemanticResult`        | Response wrapping `requests` + tabular data |

The API assumes the semantic layer is already properly configured. Some semantic layers, like Snowflake, might require additional runtime configuration. For example, Snowflake might have been configured without a default schema, so when listing semantic views it's necessary to pass additional configuration specifying the schema. This information is usually provided through a sidechannel. For Snowflake the Superset semantic layer extension uses the stored credentials and stored configuration to figure out which schemas are available, and prompts the user to choose one when browsing available views. I assume most semantic layers will ignore the extra configuration until the day additional complexity makes it necessary.

The extra configuration payload is called `runtime_configuration` when exploring views (since the configuration is transient), and `additional_configuration` when querying a specific semantic view (since the information is likely stored in the client). In retrospect, from the point of view of the semantic layer the difference is inconsequential, and we should have used `extra_configuration` for both. We might simplify it in the future.

## Media types

All payloads are JSON (`application/json`). Tabular results use:

```json
{
    "schema": [{"name": "<col>", "type": "<arrow-type-name>"}],
    "rows":   [{"<col>": <value>, ...}]
}
```

Arrow types are reported using the Arrow JSON type object's `name` value (`int`, `floating`, `utf8`, `date`, `timestamp`, …), without precision or unit parameters. Temporal values are serialised as ISO 8601 strings.

## Errors

Errors follow [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html) Problem Details for HTTP APIs. Error responses should use the `application/problem+json` media type and include a JSON object with these members:

| Member | Required | Meaning |
| ------ | -------- | ------- |
| `type` | Yes | URI reference identifying the problem type. Use `about:blank` when no more specific type is available. |
| `title` | Yes | Short, stable, human-readable summary of the problem type. |
| `status` | Yes | HTTP status code for this occurrence. It must match the HTTP response status. |
| `detail` | Yes | Human-readable explanation specific to this occurrence. Clients must not parse this field for structured information. |
| `instance` | No | URI reference identifying this specific occurrence. |

Problem-specific extension members are allowed. This specification reserves `code` for a stable machine-readable error code and `invalid_params` for request validation failures. `invalid_params` is an array of objects with `name` and `reason` fields; `name` should use a JSON Pointer when the invalid value is in a JSON request body.

Examples:

```json
{
    "type": "https://semantic-layer.example/problems/view-not-found",
    "title": "Semantic view not found",
    "status": 404,
    "detail": "Semantic view 'foo' does not exist.",
    "code": "VIEW_NOT_FOUND"
}
```

```json
{
    "type": "https://semantic-layer.example/problems/invalid-query",
    "title": "Invalid semantic query",
    "status": 400,
    "detail": "Metric 'sales.revenue' is not part of semantic view 'inventory'.",
    "code": "UNKNOWN_METRIC",
    "invalid_params": [
        {
            "name": "/query/metrics/0",
            "reason": "Unknown metric id for this semantic view."
        }
    ]
}
```

| Status | Meaning                                            |
| ------ | -------------------------------------------------- |
| 400    | Malformed payload, unknown metric/dimension id, …  |
| 404    | View does not exist                                |
| 422    | The request body failed schema validation          |
| 500    | Layer raised an unexpected exception               |

## Endpoints

### `POST /views/` (`POST /views/list` supported but deprecated)

Lists semantic views. `POST` is used because the runtime configuration is a free-form JSON object.

Request:

```json
{"runtime_configuration": {}}
```

Response:

```json
[
    {
        "name": "sales",
        "display_name": "Sales overview",
        "uid": "pandas.sales",
        "features": ["GROUP_LIMIT"]
    }
]
```

`name` is the stable identifier used in endpoint paths. The optional `display_name` is a human-readable label for presentation and may be a string or `null`. Clients should fall back to `name` when `display_name` is absent, `null`, or empty. Including it in this lightweight response lets clients render view pickers without fetching every view's full metadata.

Superset has the concept of [features](https://github.com/apache/superset/blob/97eafd6140085f81ec3e70c85215cd5e6a5608fb/superset-core/src/superset_core/semantic_layers/view.py#L33-L40) for semantic layers. They currently include:

- `ADHOC_EXPRESSIONS_IN_ORDERBY`
- `GROUP_LIMIT`
- `GROUP_OTHERS`

`GROUP_LIMIT` is a feature that allows specifying separate filter constraints for the group limit subquery. This is useful when you want to determine the top N groups using different criteria (e.g., a different time range) than the main query. For example, you might want to find the top 10 products by sales over the last 30 days, but then show daily sales for those products over the last 7 days. `GROUP_OTHERS` is an additional feature that allows the remaining categories to be grouped together in an "others" bucket. Since the implementation of these features is not trivial, they are considered optional.

(`ADHOC_EXPRESSIONS_IN_ORDERBY` should be self-explanatory.)

### `POST /views/{view_name}`

Returns a view's metadata. The body carries any `additional_configuration` the view needs to materialise (often `{}`).

Request:

```json
{"additional_configuration": {}}
```

Response:

```json
{
    "name": "sales",
    "display_name": "Sales overview",
    "uid": "pandas.sales",
    "features": ["GROUP_LIMIT"],
    "dimensions": [
        {
            "id": "sales.product_category",
            "name": "product_category",
            "type": "utf8",
            "metadata": {
                "display_name": "Product category",
                "semantic_type": "category",
                "filter": {
                    "kind": "select",
                    "operators": ["=", "!=", "IN", "NOT IN", "IS NULL", "IS NOT NULL"],
                    "default_operator": "IN",
                    "multi": true
                }
            },
            "definition": "product_category",
            "description": "The product category dimension.",
            "grain": null
        }
    ],
    "metrics": [
        {
            "id": "sales.total_revenue",
            "name": "total_revenue",
            "type": "floating",
            "metadata": {
                "display_name": "Total revenue",
                "semantic_type": "currency",
                "unit": {"kind": "currency", "code": "USD"},
                "attributes": ["certified", "core"],
                "format": {
                    "preset": "currency",
                    "precision": 2,
                    "scale": 1
                },
                "filter": {
                    "kind": "range",
                    "operators": ["=", "!=", ">", ">=", "<", "<=", "between"],
                    "default_operator": "between"
                },
                "extensions": {
                    "superset": {"d3format": "$,.2f"},
                    "google_sheets": {
                        "numberFormat": {
                            "type": "CURRENCY",
                            "pattern": "$#,##0.00"
                        }
                    }
                }
            },
            "definition": "SUM(revenue)",
            "description": "Total sales revenue.",
            "aggregation": null
        }
    ]
}
```

The full metadata response repeats the optional, nullable `display_name` from the list response. When it is a non-empty string here, it is authoritative for the materialised view.

The `metadata` attribute is optional and omitted when empty. Top-level metadata is strict: servers must emit only the documented keys below. Producer-specific, experimental, and client-native annotations belong under `extensions`.

| Key | Meaning |
| --- | ------- |
| `display_name` | Human-readable label for the column. |
| `semantic_type` | Optional business classification. Known values are `currency`, `percentage`, `proportion`, `count`, `duration`, `data_size`, `date`, `timestamp`, `identifier`, `category`, `url`, `boolean`, `number`, and `string`. |
| `unit` | Semantic unit object. |
| `attributes` | String tags or classifications associated with the column. |
| `format` | Presentation hints for clients that render values. |
| `filter` | UI hints for building filter controls; query semantics still use the `filters` request payload. |
| `extensions` | Namespaced objects for producer-specific, experimental, or client-native metadata. |

### Metadata Schema

This JSON Schema describes the portable metadata contract. Producers may omit any optional field. Consumers must tolerate absent `metadata`, absent nested objects, and unknown extension namespaces.

```json
{
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$defs": {
        "ColumnMetadata": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
                "display_name": {"type": "string"},
                "semantic_type": {
                    "type": "string",
                    "enum": [
                        "currency",
                        "percentage",
                        "proportion",
                        "count",
                        "duration",
                        "data_size",
                        "date",
                        "timestamp",
                        "identifier",
                        "category",
                        "url",
                        "boolean",
                        "number",
                        "string"
                    ]
                },
                "unit": {"$ref": "#/$defs/Unit"},
                "attributes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": true
                },
                "format": {"$ref": "#/$defs/FormatMetadata"},
                "filter": {"$ref": "#/$defs/FilterMetadata"},
                "extensions": {"$ref": "#/$defs/Extensions"}
            }
        },
        "Unit": {
            "oneOf": [
                {"$ref": "#/$defs/AtomicUnit"},
                {"$ref": "#/$defs/CompoundUnit"}
            ]
        },
        "AtomicUnit": {
            "type": "object",
            "additionalProperties": false,
            "required": ["kind"],
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "currency",
                        "time",
                        "data_size",
                        "percentage",
                        "proportion",
                        "count",
                        "unitless"
                    ]
                },
                "code": {"type": "string", "minLength": 1}
            }
        },
        "CompoundUnit": {
            "type": "object",
            "additionalProperties": false,
            "required": ["numerator", "denominator"],
            "properties": {
                "numerator": {"$ref": "#/$defs/AtomicUnit"},
                "denominator": {"$ref": "#/$defs/AtomicUnit"}
            }
        },
        "FormatMetadata": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
                "preset": {
                    "type": "string",
                    "enum": [
                        "smart_number",
                        "number",
                        "currency",
                        "percentage",
                        "duration",
                        "data_size"
                    ]
                },
                "precision": {"type": "integer", "minimum": 0},
                "scale": {"type": "number"}
            }
        },
        "FilterMetadata": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "text",
                        "number",
                        "range",
                        "date",
                        "datetime",
                        "boolean",
                        "select"
                    ]
                },
                "operators": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "=",
                            "!=",
                            ">",
                            ">=",
                            "<",
                            "<=",
                            "IN",
                            "NOT IN",
                            "IS NULL",
                            "IS NOT NULL",
                            "between",
                            "contains",
                            "starts_with",
                            "ends_with"
                        ]
                    },
                    "uniqueItems": true
                },
                "default_operator": {
                    "type": "string",
                    "enum": [
                        "=",
                        "!=",
                        ">",
                        ">=",
                        "<",
                        "<=",
                        "IN",
                        "NOT IN",
                        "IS NULL",
                        "IS NOT NULL",
                        "between",
                        "contains",
                        "starts_with",
                        "ends_with"
                    ]
                },
                "multi": {"type": "boolean"}
            }
        },
        "Extensions": {
            "type": "object",
            "additionalProperties": {
                "type": "object"
            }
        }
    },
    "$ref": "#/$defs/ColumnMetadata"
}
```

### Units

Units support atomic units and compound rate units:

```json
{"kind": "currency", "code": "USD"}
{"kind": "percentage"}
{"kind": "time", "code": "ms"}
{"kind": "count", "code": "clicks"}
{
    "numerator": {"kind": "data_size", "code": "MB"},
    "denominator": {"kind": "time", "code": "s"}
}
```

Known atomic unit kinds are `currency`, `time`, `data_size`, `percentage`, `proportion`, `count`, and `unitless`. `code` is optional for `currency` and `count`, required for `time` and `data_size`, and omitted for `percentage`, `proportion`, and `unitless`. Currency codes should use ISO 4217 values such as `USD`. Time unit codes should use common short unit symbols such as `ns`, `us`, `ms`, `s`, `min`, `h`, and `d`. Data size codes should use common byte unit symbols such as `B`, `KB`, `MB`, `GB`, and `TB`. Consumers that do not understand structured units may fall back to displaying the `code` if present, otherwise the `kind`.

### Formats

The `metadata.format` attribute is portable and contains only `preset`, `precision`, and `scale`. `format.preset` is one of `smart_number`, `number`, `currency`, `percentage`, `duration`, or `data_size`. `format.precision` is a non-negative integer describing decimal places. `format.scale` is a numeric multiplier applied to raw values before display; omitted means no scaling. For example, a value of `42` with `scale: 0.001` is displayed as `0.042` before precision and preset formatting are applied.

The `percentage` preset assumes raw values are ratios. A raw value of `0.42` with `preset: "percentage"` is displayed as `42%` by clients that follow the portable hint. Use a client-native extension when the target client needs different behavior.

Native client formats live under `metadata.extensions`, for example `extensions.superset.d3format` for Superset and `extensions.google_sheets.numberFormat` for Google Sheets/Coefficient:

```json
{
    "extensions": {
        "superset": {"d3format": "$,.2f"},
        "google_sheets": {
            "numberFormat": {
                "type": "CURRENCY",
                "pattern": "$#,##0.00"
            }
        }
    }
}
```

### Filters

The `metadata.filter` attribute is advisory metadata for query builders. Query semantics still use the `filters` request payload. Initial filter hints describe controls only; dynamic value loading is intentionally outside the metadata payload. Clients that need selectable values should call `POST /views/{view_name}/values`.

Known filter `kind` values are `text`, `number`, `range`, `date`, `datetime`, `boolean`, and `select`. Common fields are `operators`, `default_operator`, and `multi`. When present, `default_operator` must be one of `operators`; `multi` is only meaningful for `select`.

The initial portable operator vocabulary is:

| Operator | Meaning |
| -------- | ------- |
| `=` | Equal to a scalar value. |
| `!=` | Not equal to a scalar value. |
| `>` | Greater than a scalar value. |
| `>=` | Greater than or equal to a scalar value. |
| `<` | Less than a scalar value. |
| `<=` | Less than or equal to a scalar value. |
| `IN` | In a list of values. |
| `NOT IN` | Not in a list of values. |
| `IS NULL` | Value is null; `value` should be `null`. |
| `IS NOT NULL` | Value is not null; `value` should be `null`. |
| `between` | Between two boundary values; `value` should be a two-element list. |
| `contains` | Text contains a substring. |
| `starts_with` | Text starts with a substring. |
| `ends_with` | Text ends with a substring. |

### Extensions

Extension namespaces are objects keyed by stable producer or client names. Known examples are `superset`, `google_sheets`, `datajunction`, `quiver`, and `custom`. Servers must move unknown top-level producer metadata under an extension namespace instead of emitting it at the metadata top level. Clients must ignore unknown extension namespaces and unknown fields inside known extension namespaces.

### `POST /views/{view_name}/query`

Run a semantic query and return the resulting table.

Request mirrors `SemanticQuery`, identifying metrics and dimensions by stable id:

```json
{
    "additional_configuration": {},
    "query": {
        "metrics":    ["sales.total_revenue"],
        "dimensions": ["sales.product_category"],
        "filters": [
            {
                "type":     "WHERE",
                "column":   "sales.region",
                "operator": "=",
                "value":    "North"
            }
        ],
        "order": [{"by": "sales.total_revenue", "direction": "DESC"}],
        "limit":  100,
        "offset": 0,
        "group_limit": {
            "dimensions":   ["sales.product_category"],
            "top":          5,
            "metric":       "sales.total_revenue",
            "direction":    "DESC",
            "group_others": false,
            "filters":      []
        }
    }
}
```

Filter `column` is an id; if `type` is `HAVING` it references a metric, if `WHERE` it references a dimension. `value` is a literal, a list (for `IN` / `NOT IN`), or `null` (for `IS NULL` / `IS NOT NULL`).

The response is a `SemanticResult`:

```json
{
    "requests": [{"type": "pandas", "definition": "SELECT METRICS ..."}],
    "results": {
        "schema": [
            {"name": "product_category", "type": "utf8"},
            {"name": "total_revenue", "type": "floating"}
        ],
        "rows": [
            {"product_category": "Electronics", "total_revenue": 5126.5}
        ]
    }
}
```

Result schemas intentionally include only `name` and `type`; column metadata is discoverable from `POST /views/{view_name}`.

### `POST /views/{view_name}/row-count`

Identical request body to `…/query`; returns a single-row table with a `COUNT` column.

### `POST /views/{view_name}/values`

Distinct values for a dimension, optionally filtered.

Request:

```json
{
    "additional_configuration": {},
    "dimension": "sales.region",
    "filters":   []
}
```

Response: a `SemanticResult` whose `rows` contain the unique values.

### `POST /views/{view_name}/compatible-metrics`

In some semantic layers not all metrics and dimensions in a given semantic view are compatible. This is true for MetricFlow, but not for Snowflake or DataJunction. When building charts in Superset this endpoint and the `compatible-dimensions` endpoint are called as metrics or dimensions are selected, narrowing down the set of valid metrics and dimensions.

Request:

```json
{
    "additional_configuration": {},
    "selected_metrics":    ["sales.total_revenue"],
    "selected_dimensions": ["sales.region"]
}
```

Response: an array of metric objects.

### `POST /views/{view_name}/compatible-dimensions`

Same shape as `compatible-metrics`, returning dimensions.

## Changelog

### Unreleased

- Semantic views may now expose a top-level, nullable `display_name` in list and metadata responses. Clients fall back to the stable `name` when it is absent, `null`, or empty.
- Error responses now follow RFC 9457 Problem Details for HTTP APIs and should use `application/problem+json`.
- Column metadata may now be exposed on dimension and metric objects returned by `POST /views/{view_name}`. The optional `metadata` object now has a strict top-level contract: `display_name`, `semantic_type`, `unit`, `attributes`, `format`, `filter`, and `extensions`.
- Added a formal JSON Schema for portable column metadata, including `unit`, `format`, `filter`, and `extensions`.
- `metadata.unit` is documented as an object-only atomic or compound unit. Consumers may tolerate legacy string units during migration, but conforming servers should not emit string units.
- Added portable `metadata.format` for presentation hints with `preset`/`precision`/`scale` fields, including explicit `scale` and percentage semantics. Native client formats such as Superset D3 and Google Sheets number formats belong under namespaced `metadata.extensions`.
- Clarified that `metadata.filter` is advisory query-builder metadata. The first version describes controls/operators/defaults only and does not embed dynamic value endpoint references. Clients should use `POST /views/{view_name}/values` for dynamic selectable values.
- Result schemas remain metadata-free. Clients should discover column metadata from `POST /views/{view_name}` and use `results.schema` only for tabular output column names and Arrow type names.
- Arrow types are reported using the Arrow JSON type object's `name` value (`int`, `floating`, `utf8`, `date`, `timestamp`, …), not PyArrow's canonical string representation (`int64`, `double`, `string`, `date32[day]`, …).

## Conformance

A server is conformant if, for every method on `SemanticView`, the
corresponding endpoint:

1. resolves metric/dimension references by id against the view's `get_metrics()` / `get_dimensions()` before invoking the underlying call;
2. returns `SemanticResult` payloads with the request log preserved verbatim;
3. reports column types using Arrow JSON type names, including `floating` for floating-point values;
4. omits `metadata` when empty;
5. emits only documented top-level metadata keys and places producer-specific, experimental, or client-native metadata under `extensions`;
6. emits RFC 9457 problem details for error responses.

A client is conformant if it:

1. uses `name` when a view's optional `display_name` is absent, `null`, or empty;
2. resolves result column metadata from `POST /views/{view_name}` and treats `results.schema` as tabular output only;
3. tolerates absent `metadata` and absent nested metadata objects;
4. ignores unknown extension namespaces and unknown fields inside known extension namespaces;
5. treats `metadata.filter` as advisory UI metadata and uses the query `filters` payload for query semantics.
