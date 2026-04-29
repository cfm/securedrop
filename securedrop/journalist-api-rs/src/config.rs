use serde::Deserialize;

#[derive(Clone, Deserialize)]
pub struct Config {
    pub server: ServerConfig,
    pub database: DatabaseConfig,
    pub python_backend: PythonBackendConfig,
}

#[derive(Clone, Deserialize)]
pub struct ServerConfig {
    pub listen: String,
}

#[derive(Clone, Deserialize)]
pub struct DatabaseConfig {
    pub path: String,
    #[serde(default = "default_busy_timeout")]
    pub busy_timeout_ms: u64,
}

fn default_busy_timeout() -> u64 {
    5000
}

#[derive(Clone, Deserialize)]
pub struct PythonBackendConfig {
    pub token_url: String,
    pub auth_check_url: String,
}
