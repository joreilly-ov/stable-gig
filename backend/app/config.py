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

    # Stripe payment provider (optional — 503 returned if not set)
    stripe_secret_key:      str = ""   # sk_live_… / sk_test_…
    stripe_publishable_key: str = ""   # pk_live_… / pk_test_… (safe to expose to frontend)
    stripe_webhook_secret:  str = ""   # whsec_… from Stripe Dashboard → Webhooks


settings = Settings()
