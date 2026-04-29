mod config;
mod db;
mod errors;
mod index;
mod token;

use anyhow::Result;
use axum::{
    routing::{get, post},
    Router,
};
use std::sync::Arc;
use tokio::net::TcpListener;
use tracing_subscriber::EnvFilter;

use config::Config;
use db::DbPool;

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub pool: Arc<DbPool>,
    pub http_client: reqwest::Client,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .init();

    let config_path = std::env::args()
        .skip_while(|a| a != "--config")
        .nth(1)
        .unwrap_or_else(|| "/etc/securedrop/journalist-api.toml".to_string());

    let config: Config = {
        let s = std::fs::read_to_string(&config_path)
            .map_err(|e| anyhow::anyhow!("failed to read config {config_path}: {e}"))?;
        toml::from_str(&s)?
    };

    let listen = config.server.listen.clone();

    let pool = Arc::new(db::open_pool(
        &config.database.path,
        config.database.busy_timeout_ms,
    )?);

    let http_client = reqwest::Client::builder()
        .pool_max_idle_per_host(4)
        .build()?;

    let state = AppState {
        config: Arc::new(config),
        pool,
        http_client,
    };

    let app = Router::new()
        .route("/api/v1/token", post(token::handle_token))
        .route("/api/v2/index", get(index::handle_index))
        .route("/api/v2/index/*shard_spec", get(index::handle_index_sharded))
        .with_state(state);

    let listener = TcpListener::bind(&listen).await?;
    tracing::info!("listening on {listen}");
    axum::serve(listener, app).await?;

    Ok(())
}
