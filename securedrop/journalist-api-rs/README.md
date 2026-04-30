# Plan: Rust Service for Heavy Journalist API Endpoints

## Context

Apache/mod_wsgi keeps Python interpreter processes alive between requests, and two journalist API routes — `POST /api/v1/token` and `GET /api/v2/index` — load the entire database into Python objects on every call, causing fragmentation-style heap bloat (glibc won't release pages back; `malloc_trim(0)` was added as a band-aid in `journalist_app/__init__.py:154`).

- `/api/v1/token` is heavy because clients send `Prefer: securedrop=4`, causing Python to call `get_index_hints()` and load the entire DB. Fix: Rust strips the `Prefer` header before forwarding to Python, so Python handles auth normally but skips index loading. When the client wants hints (Prefer ≥ 4), Rust computes them from SQLite and injects them into Python's response.
- `/api/v2/index` is heavy because it loads all sources/items/journalists. Fix: Rust handles it directly from SQLite, delegating only the auth check back to Python via a new lightweight endpoint.

Rust never implements authentication, never reads tokens, and never touches Redis. All session and credential logic stays in Python.

---

## Architecture

```
Client → Apache :8080
              ├── <Location /api/v1/token>  → ProxyPass → Rust :8082 → strips Prefer → Apache :8080/api/v1/_token → Python
              │                                                        → injects hints if Prefer ≥ 4
              ├── <Location /api/v2/index>  → ProxyPass → Rust :8082 → auth check → Apache :8080/api/v1/_auth → Python (200/403)
              │                                                        └─ on 200 → SQLite queries → BLAKE2s → response
              │   (no shard_spec support — no client uses it)
              └── WSGIScriptAlias /          → mod_wsgi → Flask (unchanged)
```

**Proxy-loop prevention**: Apache proxies `:8080 /api/v1/token` → Rust, so Rust cannot call `/api/v1/token` on `:8080` without looping. Instead, Flask exposes alias routes at `/api/v1/_token` and `/api/v1/_auth` — paths not matched by any `<Location>` block — so Rust calls those directly on port 8080. No second Apache VirtualHost needed.

---

## Python Routes

In `securedrop/journalist_app/api.py` inside `make_blueprint()`, both `get_token` and `check_auth` carry stacked route decorators so they are reachable at both the public path and the Rust-internal alias:

```python
@api.route("/token", methods=["POST"])
@api.route("/_token", methods=["POST"])
def get_token() -> tuple[flask.Response, int]:
    ...  # existing implementation unchanged

@api.route("/auth", methods=["GET"])
@api.route("/_auth", methods=["GET"])
def check_auth() -> tuple[flask.Response, int]:
    return jsonify({}), 200
```

Stacked decorators share the same endpoint name, so the `_insecure_api_views` check and `validate_data` `before_request` hook apply identically to both paths.
