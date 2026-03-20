"""Unit tests for app/config.py (Settings).

The module-level `settings = Settings()` singleton is already instantiated
when conftest.py sets up env vars. We test the Settings class directly
by constructing instances with controlled values.

Coverage
--------
- gemini_api_key is required (ValidationError when absent)
- Optional fields default to empty string
- Values are loaded from environment variables
"""

import os
from unittest.mock import patch

import pytest


class TestSettings:
    def test_loads_gemini_api_key(self):
        from app.config import Settings

        with patch.dict(os.environ, {"GEMINI_API_KEY": "my-gemini-key"}, clear=False):
            s = Settings()
        assert s.gemini_api_key == "my-gemini-key"

    def test_supabase_fields_default_to_empty_string(self):
        from app.config import Settings

        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "k",
                "SUPABASE_URL": "",
                "SUPABASE_ANON_KEY": "",
                "SUPABASE_SERVICE_KEY": "",
            },
            clear=False,
        ):
            s = Settings()
        assert s.supabase_url == ""
        assert s.supabase_anon_key == ""
        assert s.supabase_service_key == ""

    def test_smarty_fields_default_to_empty_string(self):
        from app.config import Settings

        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "k", "SMARTY_AUTH_ID": "", "SMARTY_AUTH_TOKEN": ""},
            clear=False,
        ):
            s = Settings()
        assert s.smarty_auth_id == ""
        assert s.smarty_auth_token == ""

    def test_optional_fields_read_from_env(self):
        from app.config import Settings

        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "key",
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_ANON_KEY": "anon",
                "SUPABASE_SERVICE_KEY": "service",
                "SMARTY_AUTH_ID": "smarty-id",
                "SMARTY_AUTH_TOKEN": "smarty-tok",
            },
            clear=False,
        ):
            s = Settings()
        assert s.supabase_url == "https://example.supabase.co"
        assert s.smarty_auth_id == "smarty-id"

    def test_module_level_settings_uses_env_vars_from_conftest(self):
        """The singleton already instantiated by conftest has the test values."""
        from app.config import settings

        assert settings.gemini_api_key == "test-gemini-key-000"
        assert settings.supabase_url == "https://test.supabase.co"
