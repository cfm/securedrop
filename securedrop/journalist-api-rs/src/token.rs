use axum::{
    body::Bytes,
    extract::State,
    http::{HeaderMap, HeaderName, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
};
use serde_json::json;
use std::str::FromStr;

use crate::{db, AppState};

static STRIP_HEADERS: &[&str] = &[
    "host",
    "prefer",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
];

/// Returns true if the client sent `Prefer: securedrop=N` with N >= 4.
/// Mirrors Python's `get_request_minor_version(strict=True) >= 4`.
fn client_wants_hints(headers: &HeaderMap) -> bool {
    headers
        .get("prefer")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.split('=').nth(1))
        .and_then(|n| n.parse::<u8>().ok())
        .map(|n| n >= 4)
        .unwrap_or(false)
}

pub async fn handle_token(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let url = &state.config.python_backend.token_url;
    let wants_hints = client_wants_hints(&headers);

    tracing::debug!(url = %url, body_len = body.len(), wants_hints, "token: forwarding to python");

    let mut req_builder = state.http_client.post(url).body(body.to_vec());

    for (name, value) in &headers {
        if STRIP_HEADERS.contains(&name.as_str()) {
            tracing::debug!(header = %name, "token: stripping request header");
            continue;
        }
        if let Ok(v) = value.to_str() {
            tracing::debug!(header = %name, "token: forwarding request header");
            req_builder = req_builder.header(name.as_str(), v);
        }
    }

    let resp = match req_builder.send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("token proxy error: {e}");
            return StatusCode::BAD_GATEWAY.into_response();
        }
    };

    let upstream_status = resp.status().as_u16();
    tracing::info!(upstream_status, "token: python response");

    let status = StatusCode::from_u16(upstream_status)
        .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);

    let mut response_headers = HeaderMap::new();
    for (name, value) in resp.headers() {
        if STRIP_HEADERS.contains(&name.as_str()) {
            tracing::debug!(header = %name, "token: stripping response header");
            continue;
        }
        if let (Ok(hn), Ok(hv)) = (
            HeaderName::from_str(name.as_str()),
            HeaderValue::from_bytes(value.as_bytes()),
        ) {
            tracing::debug!(
                header = %name,
                value = ?value.to_str().unwrap_or("<binary>"),
                "token: forwarding response header"
            );
            response_headers.insert(hn, hv);
        }
    }

    let body_bytes = match resp.bytes().await {
        Ok(b) => b,
        Err(e) => {
            tracing::error!("token proxy body error: {e}");
            return StatusCode::BAD_GATEWAY.into_response();
        }
    };

    // Inject hints if the client requested minor version >= 4 and login succeeded.
    // On any error (DB or parse) we fall through and return the unmodified body so
    // that a hints failure never breaks authentication.
    let body_bytes = if wants_hints && status == StatusCode::OK {
        inject_hints(body_bytes, &state).await
    } else {
        body_bytes
    };

    tracing::info!(client_status = status.as_u16(), "token: returning to client");

    (status, response_headers, body_bytes).into_response()
}

async fn inject_hints(body_bytes: Bytes, state: &AppState) -> Bytes {
    let pool = state.pool.clone();
    let index = match tokio::task::spawn_blocking(move || db::build_index(&pool, 4)).await {
        Ok(Ok(idx)) => idx,
        Ok(Err(e)) => {
            tracing::error!("hints db error: {e}");
            return body_bytes;
        }
        Err(e) => {
            tracing::error!("hints spawn_blocking panic: {e}");
            return body_bytes;
        }
    };

    let version = db::json_version(&index);
    let sources = index
        .get("sources")
        .and_then(|v| v.as_object())
        .map(|m| m.len())
        .unwrap_or(0);
    let items = index
        .get("items")
        .and_then(|v| v.as_object())
        .map(|m| m.len())
        .unwrap_or(0);

    let mut body_json = match serde_json::from_slice::<serde_json::Value>(&body_bytes) {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("hints: could not parse python response body: {e}");
            return body_bytes;
        }
    };

    if let Some(obj) = body_json.as_object_mut() {
        obj.insert(
            "hints".to_string(),
            json!({"version": version, "sources": sources, "items": items}),
        );
        tracing::debug!(sources, items, "token: injected hints");
        if let Ok(new_body) = serde_json::to_vec(&body_json) {
            return new_body.into();
        }
    }

    body_bytes
}
