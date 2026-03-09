from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Gemini
    gemini_api_key: str

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""  # Bypasses RLS — admin operations only

    # Smarty address API
    smarty_auth_id: str = ""
    smarty_auth_token: str = ""


settings = Settings()
