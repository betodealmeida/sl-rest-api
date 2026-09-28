# Quiver Semantic Layer extension

Superset semantic-layer extension for a Quiver gateway that implements the
Semantic Layer REST API documented in this repository's `SPEC.md`.

The extension keeps OAuth2 client configuration in the encrypted semantic
layer configuration and stores each user's access and refresh tokens in
Superset's backend-only extension secret storage. Tokens are scoped by the
extension, Superset user, Quiver API URL, and OAuth2 client ID; they are not
tied to a semantic-layer UUID and are never exposed to browser code.

## Configuration

- `api_url`: Quiver Semantic Layer REST API base URL.
- `client_id` and `client_secret`: OAuth2 client credentials.
- `authorization_url` and `token_url`: optional overrides; by default these
  are derived as `<api_url>/authorize` and `<api_url>/token`.
- `scope`: optional OAuth2 scope string.

The OAuth redirect URI is:

```text
/extensions/netflix/quiver-semantic-layer/oauth/callback
```

Register the fully-qualified URI with the OAuth2 provider.
