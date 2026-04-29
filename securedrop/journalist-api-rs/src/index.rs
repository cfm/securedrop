use axum::{
    extract::State,
    http::{HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde_json::json;

use crate::{db, errors, AppState};

const API_MINOR_VERSION: u8 = 4;

pub async fn handle_index_sharded(
    State(_state): State<AppState>,
    _headers: HeaderMap,
) -> Response {
    errors::json_error(
        StatusCode::NOT_IMPLEMENTED,
        "Not Implemented",
        "Sharding is not supported by this endpoint",
    )
}

pub async fn handle_index(State(state): State<AppState>, headers: HeaderMap) -> Response {
    let has_auth = headers.contains_key("authorization");
    tracing::debug!(has_auth, "index: sending auth check to python");

    let mut auth_req = state
        .http_client
        .get(&state.config.python_backend.auth_check_url);
    if let Some(auth) = headers.get("authorization") {
        if let Ok(auth_str) = auth.to_str() {
            auth_req = auth_req.header("authorization", auth_str);
        }
    }

    let auth_resp = match auth_req.send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("auth check error: {e}");
            return StatusCode::BAD_GATEWAY.into_response();
        }
    };

    let auth_status = auth_resp.status().as_u16();
    tracing::info!(auth_status, "index: python auth response");

    if auth_resp.status() == reqwest::StatusCode::FORBIDDEN {
        return errors::json_error(
            StatusCode::FORBIDDEN,
            "Forbidden",
            "Token authentication failed.",
        );
    }
    if !auth_resp.status().is_success() {
        return StatusCode::BAD_GATEWAY.into_response();
    }

    let minor = parse_minor_version(&headers);
    tracing::debug!(minor, "index: building index");

    let pool = state.pool.clone();
    let index = match tokio::task::spawn_blocking(move || db::build_index(&pool, minor)).await {
        Ok(Ok(idx)) => idx,
        Ok(Err(e)) => {
            tracing::error!("db error building index: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
        Err(e) => {
            tracing::error!("spawn_blocking panic: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let etag_hash = db::json_version(&index);
    let etag_value = format!(r#""{etag_hash}""#);

    if let Some(inm) = headers.get("if-none-match") {
        let inm_str = inm.to_str().unwrap_or("");
        tracing::debug!(if_none_match = %inm_str, etag = %etag_hash, "index: conditional request");
        if inm_str == etag_value {
            tracing::info!(client_status = 304u16, "index: returning to client");
            let mut resp = StatusCode::NOT_MODIFIED.into_response();
            resp.headers_mut().insert(
                "etag",
                HeaderValue::from_str(&etag_value).unwrap(),
            );
            return resp;
        }
    }

    tracing::info!(client_status = 200u16, "index: returning to client");
    let mut resp = Json(json!(index)).into_response();
    resp.headers_mut()
        .insert("etag", HeaderValue::from_str(&etag_value).unwrap());
    resp
}

fn parse_minor_version(headers: &HeaderMap) -> u8 {
    let prefer = match headers.get("prefer") {
        Some(v) => v.to_str().unwrap_or("").to_string(),
        None => return API_MINOR_VERSION,
    };
    if let Some(val) = prefer.split('=').nth(1) {
        if let Ok(n) = val.parse::<u8>() {
            if n <= API_MINOR_VERSION {
                return n;
            }
        }
    }
    API_MINOR_VERSION
}
