# Semantic Layer REST API clients

Bundles the three pieces that let Apache Superset talk to a Semantic Layer REST API server:

| Layer       | Module                            | Plugin host           |
| ----------- | --------------------------------- | --------------------- |
| HTTP client | `sl_db_engine_spec.adapter`       | Shillelagh            |
| SQL bridge  | `sl_db_engine_spec.dialect`       | SQLAlchemy            |
| Superset UI | `sl_db_engine_spec.engine_spec`   | Superset              |

Each piece is registered through the standard entry-point group for its host (`shillelagh.adapter`, `sqlalchemy.dialects`, `superset.db_engine_specs`), so installing this package is all that's needed. No patching, no manual import.

## Install

In your Superset environment:

```bash
pip install sl-db-engine-spec/client/
```

Restart Superset and the **Semantic Layer API** entry will show up in the "Connect a database" dialog.

## Connection parameters

The connection form asks for:

- **Host** (required) — e.g. `localhost`
- **Port** — optional
- **Secure** — toggle for HTTPS
- **Additional configuration** — JSON object forwarded to the server on every request (sent as `runtime_configuration` when listing views and as `additional_configuration` for each view).
- **OAuth2 client information** — `{id, secret, scope?}`. The `authorization_request_uri` and `token_request_uri` are auto-filled from the host:port as `http(s)://host:port/authorize` and `.../token`; explicit overrides are preserved.

## URL form

If you'd rather skip the form, the equivalent SQLAlchemy URL is:

```
semanticapi://<host>[:port]/?encryption=<true|false>&additional_configuration=<urlencoded JSON>
```

## Standalone use (no Superset)

The adapter and dialect work without Superset:

```python
from sqlalchemy import create_engine, text

engine = create_engine("semanticapi://localhost:8000/")
sql = """
SELECT
  hw_category AS hw_category,
  dateint AS dateint,
  completed_downloads AS completed_downloads
FROM main."demo.metrics.main.thumbs_demo"
GROUP BY
  hw_category,
  dateint
ORDER BY
  completed_downloads DESC
LIMIT 100
OFFSET 0
"""
with engine.connect() as c:
    print(c.execute(text(sql)).fetchall())
```

OAuth2 access tokens can be passed in the URL as `?access_token=...`.

## Browsing cubes in Superset

The first two dot-delimited parts of a view name are presented as a catalog and
schema. For example,
`demo.metrics.add_thumbs_demo_materialization.thumbs_cov_geo` appears under
catalog `demo` and schema `metrics`. Superset still stores and queries the full
view name as the table name, so the remaining parts are never lost. View names
with fewer than three nonempty parts are not shown in the catalog/schema picker.

The selected catalog and schema narrow the discovered tables and the adapter's
registered tables for that connection. To browse multiple catalogs in Superset,
enable **Allow changing catalogs** for the database. After upgrading an existing
database connection, sync its permissions and grant access to the new catalog
and schema permissions; old `main` schema permissions do not cover them.
