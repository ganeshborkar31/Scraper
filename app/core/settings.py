from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    app_secret_key: str = "development-only-change-me"
    database_url: str = "sqlite:///./business.db"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    admin_username: str = "admin"
    admin_password: str = "change-this-for-local-development"
    public_contact_email: str = ""


settings = Settings()
