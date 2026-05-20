from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    hermes_env: str = "development"
    hermes_log_level: str = "INFO"
    hermes_port: int = 8090
    hermes_halted: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://hermes:hermes@localhost:5432/hermes"
    redis_url: str = "redis://localhost:6379/0"

    # Alpaca
    alpaca_mode: str = "paper"
    alpaca_paper_api_key: str = ""
    alpaca_paper_secret_key: str = ""
    alpaca_paper_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_base_url: str = "https://data.alpaca.markets"
    alpaca_live_api_key: str = ""
    alpaca_live_secret_key: str = ""

    # IBKR
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4001
    ibkr_client_id: int = 1
    ibkr_account: str = ""

    @property
    def ibkr_configured(self) -> bool:
        return bool(self.ibkr_host and self.ibkr_port)

    # Telegram
    hermes_telegram_bot_token: str = ""
    hermes_telegram_chat_id: str = ""

    @property
    def alpaca_configured(self) -> bool:
        if self.alpaca_mode == "paper":
            return bool(self.alpaca_paper_api_key and self.alpaca_paper_secret_key)
        return bool(self.alpaca_live_api_key and self.alpaca_live_secret_key)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.hermes_telegram_bot_token and self.hermes_telegram_chat_id)


settings = Settings()
