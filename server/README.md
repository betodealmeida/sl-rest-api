# Semantic Layer REST API reference server

A Litestar reference implementation of the protocol described in [`SPEC.md`](../SPEC.md). This server speaks for the Pandas semantic layer from [`betodealmeida/pandas-semantic-layer`](https://github.com/betodealmeida/pandas-semantic-layer).

## Run

```bash
git clone https://github.com/apache/superset.git
pip install superset/superset-core

# install the Pandas semantic layer implementation
git clone https://github.com/betodealmeida/pandas-semantic-layer.git
pip install pandas-semantic-layer/backend

git clone https://github.com/betodealmeida/sl-db-engine-spec.git
cd sl-db-engine-spec/server/
pip install -e .

# optional: pass the layer's configuration as JSON
export SEMANTIC_LAYER_CONFIGURATION='{"dataset": "sales"}'

semantic-api
# or
litestar --app semantic_api.app:app run
```

OpenAPI docs are served at `/schema`. Swagger UI at `/schema/swagger`.

## View discovery

The sample views live at two different depths in a three-level hierarchy:

| View | Group | Area | Variant |
| --- | --- | --- | --- |
| `marketing` | `foo` | `bar` | `null` |
| `sales` | `foo` | `bar` | `baz` |

The `null` value means that `marketing` has no variant. The view names remain
`marketing` and `sales` in `/views/` and in query URLs; their discovery
locations can change without changing those identifiers.

Start at the root and send each returned value as the next path element:

```bash
curl localhost:8000/discover -H 'content-type: application/json' -d '{}'
curl localhost:8000/discover -H 'content-type: application/json' -d '{"path":["foo"]}'
curl localhost:8000/discover -H 'content-type: application/json' -d '{"path":["foo","bar"]}'
```

The final request returns `"values": ["baz", null]` with no `next_level`.
Filter views using the selected path:

```bash
curl localhost:8000/views/ -H 'content-type: application/json' -d '{"path":["foo","bar",null]}'
curl localhost:8000/views/ -H 'content-type: application/json' -d '{"path":["foo","bar","baz"]}'
```

## Quick start

```bash
# 1. list views
curl localhost:8000/views/ -H 'content-type: application/json' -d '{}'

# 2. inspect a view
curl localhost:8000/views/sales -H 'content-type: application/json' -d '{}'

# 3. query it
curl localhost:8000/views/sales/query \
    -H 'content-type: application/json' \
    -d '{
        "query": {
            "metrics":    ["sales.total_revenue"],
            "dimensions": ["sales.product_category"]
        }
    }'
```
