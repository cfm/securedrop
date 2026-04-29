use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde_json::json;

pub fn json_error(status: StatusCode, name: &str, message: &str) -> Response {
    (status, Json(json!({"error": name, "message": message}))).into_response()
}
