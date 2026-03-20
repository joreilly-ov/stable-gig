"""Tests for main.py — JSON logging formatter.

main.py cannot be imported directly in the test suite because of a
circular import: auth.py does `from main import limiter` at module level
and conftest.py already inserts a fake `main` stub to resolve this.

We test `_JsonFormatter` by loading main.py as a separate module alias
so the formatter logic can be exercised in isolation without disturbing
the fake stub that the rest of the test suite relies on.
"""

import importlib.util
import json
import logging
import pathlib
import sys


def _load_main_module():
    """Load main.py under the alias '_main_under_test' without overwriting sys.modules['main']."""
    path = pathlib.Path(__file__).parent.parent / "main.py"
    spec = importlib.util.spec_from_file_location("_main_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load once for the whole module
_main = _load_main_module()
_JsonFormatter = _main._JsonFormatter


# ---------------------------------------------------------------------------
# _JsonFormatter
# ---------------------------------------------------------------------------

class TestJsonFormatter:
    def _format(self, message: str, level=logging.INFO, **extra) -> dict:
        formatter = _JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=level,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        raw = formatter.format(record)
        return json.loads(raw)

    def test_output_is_valid_json(self):
        formatter = _JsonFormatter()
        record = logging.LogRecord("t", logging.INFO, "", 0, "hello", (), None)
        raw = formatter.format(record)
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    def test_severity_field_matches_level(self):
        entry = self._format("msg", level=logging.WARNING)
        assert entry["severity"] == "WARNING"

    def test_message_field_present(self):
        entry = self._format("test message")
        assert entry["message"] == "test message"

    def test_logger_field_present(self):
        entry = self._format("msg")
        assert "logger" in entry

    def test_extra_fields_forwarded(self):
        entry = self._format("msg", user_id="uid-123", upload_filename="clip.mp4")
        assert entry["user_id"] == "uid-123"
        assert entry["upload_filename"] == "clip.mp4"

    def test_exception_info_included(self):
        formatter = _JsonFormatter()
        try:
            raise ValueError("something went wrong")
        except ValueError:
            import sys as _sys
            exc_info = _sys.exc_info()

        record = logging.LogRecord("t", logging.ERROR, "", 0, "error", (), exc_info)
        raw = formatter.format(record)
        parsed = json.loads(raw)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]

    def test_output_is_single_line(self):
        entry_str = json.dumps(self._format("msg"))
        assert "\n" not in entry_str
