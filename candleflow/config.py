from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://candleflow:candleflow@localhost:5432/candleflow"
    redis_url: str = "redis://localhost:6379/0"
    rates_url: str = "http://localhost:8003"
    marking_url: str = "http://localhost:8001"
    ai_provider: str = "fake"
    openai_api_key: str = ""
    primary_model: str = "gpt-5.4-mini"
    fallback_model: str = "gpt-6-luna"
    llm_timeout: int = 45
    # Per API process. Keep processes x (size + overflow) below PostgreSQL max_connections.
    db_pool_size: int = 10
    db_max_overflow: int = 5
    rate_limits: bool = True
    log_level: str = "INFO"


settings = Settings()
