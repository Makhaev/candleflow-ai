from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://candleflow:candleflow@localhost:5432/candleflow"
    redis_url: str = "redis://localhost:6379/0"
    rates_url: str = "http://localhost:8003"
    marking_url: str = "http://localhost:8001"
    ai_provider: str = "fake"
    openai_api_key: str = ""
    primary_model: str = "gpt-5-mini"
    fallback_model: str = "gpt-6-luna"
    llm_timeout: int = 45


settings = Settings()
