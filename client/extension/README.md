# Semantic Layer API extension

Superset extension for any service that implements the Semantic Layer REST API
documented in this repository's `SPEC.md`. It supports OAuth2 authorization-code
authentication and access tokens.

The extension keeps OAuth2 client configuration in the encrypted semantic
layer configuration and stores each user's access and refresh tokens in
Superset's backend-only extension secret storage. Tokens are scoped by the
extension, Superset user, API URL, and OAuth2 client ID; they are not
tied to a semantic-layer UUID and are never exposed to browser code.

## Configuration

- `api_url`: Semantic Layer REST API base URL.
- `auth_type`: `oauth2` or `access_token`.
- `access_token`: bearer token when using access-token authentication.
- `client_id` and `client_secret`: OAuth2 client credentials when using OAuth2.
- `authorization_url` and `token_url`: optional overrides; by default these
  are derived as `<api_url>/authorize` and `<api_url>/token`.
- `scope`: optional OAuth2 scope string.

After authentication, the extension calls `POST /views` with a `path` to
populate hierarchy selectors and list views at the selected location. The
response's `hierarchy.next` names the next selector; `null` marks the final
level. A semantic layer can fix any leading selections.
When adding a semantic view, those selections are locked and the remaining
levels can be selected. The extension verifies the fixed path again when a
saved semantic view is used.
The extension does not use the older `/views/list` route.

The OAuth redirect URI is:

```text
/extensions/betodealmeida/semantic-layer-api/oauth/callback
```

Register the fully-qualified URI with the OAuth2 provider.
