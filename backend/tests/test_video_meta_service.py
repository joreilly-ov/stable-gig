"""Unit tests for app/services/video_meta.py.

hachoir and mutagen are imported lazily *inside* extract_video_metadata(),
so we can inject mocks via patch.dict(sys.modules, ...) at call time.

Coverage
--------
hachoir path:
  - duration, resolution, frame_rate, creation_date extracted correctly
  - invalid frame_rate string silently skipped
  - createParser returning None handled gracefully
  - hachoir import failure → falls back silently

mutagen path:
  - GPS (+lat +lon) parsed correctly
  - GPS (-lat +lon) parsed correctly
  - Malformed GPS string stores gps_raw but no lat/lon
  - device_make and device_model extracted
  - recorded_at extracted from ©day tag
  - duration fallback from mutagen.info.length when hachoir absent
  - mutagen import failure → falls back silently

Combined:
  - Both libraries fail → empty dict returned (never raises)
  - Return type is always dict
"""

import sys
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services.video_meta import extract_video_metadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hachoir_mods(mock_hm=None, mock_parser=None):
    """Return a sys.modules patch dict for hachoir with controllable metadata."""
    if mock_parser is None:
        mock_parser = MagicMock()
        mock_parser.__enter__ = lambda self: self
        mock_parser.__exit__ = MagicMock(return_value=False)

    mod_parser = MagicMock()
    mod_parser.createParser.return_value = mock_parser

    mod_metadata = MagicMock()
    mod_metadata.extractMetadata.return_value = mock_hm

    return {
        "hachoir": MagicMock(),
        "hachoir.parser": mod_parser,
        "hachoir.metadata": mod_metadata,
    }


def _mutagen_mods(tags_dict: dict, duration: float = 30.0):
    """Return a sys.modules patch dict for mutagen.mp4 with controllable tags."""
    mock_tags = MagicMock()
    mock_tags.info.length = duration
    mock_tags.get.side_effect = lambda key: tags_dict.get(key)

    mod_mp4 = MagicMock()
    mod_mp4.MP4.return_value = mock_tags

    return {"mutagen": MagicMock(), "mutagen.mp4": mod_mp4}


# ---------------------------------------------------------------------------
# hachoir extraction
# ---------------------------------------------------------------------------

class TestHachoirExtraction:
    def test_extracts_duration(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        mock_hm = MagicMock()
        mock_hm.has.side_effect = lambda k: k == "duration"
        mock_hm.get.side_effect = lambda k: timedelta(seconds=45.5)

        with patch.dict(sys.modules, _hachoir_mods(mock_hm)):
            result = extract_video_metadata(str(f))

        assert result["duration_seconds"] == 45.5

    def test_extracts_resolution(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        mock_hm = MagicMock()
        mock_hm.has.side_effect = lambda k: k in {"width", "height"}
        mock_hm.get.side_effect = lambda k: {"width": 1920, "height": 1080}[k]

        with patch.dict(sys.modules, _hachoir_mods(mock_hm)):
            result = extract_video_metadata(str(f))

        assert result["resolution"] == "1920x1080"

    def test_extracts_frame_rate(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        mock_hm = MagicMock()
        mock_hm.has.side_effect = lambda k: k == "frame_rate"
        mock_hm.get.side_effect = lambda k: "30.0"

        with patch.dict(sys.modules, _hachoir_mods(mock_hm)):
            result = extract_video_metadata(str(f))

        assert result["frame_rate_fps"] == pytest.approx(30.0)

    def test_extracts_all_fields_together(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        data = {
            "duration": timedelta(seconds=60.0),
            "width": 1280,
            "height": 720,
            "frame_rate": "25.0",
            "creation_date": "2024-01-15 10:30:00",
        }
        mock_hm = MagicMock()
        mock_hm.has.side_effect = lambda k: k in data
        mock_hm.get.side_effect = lambda k: data[k]

        with patch.dict(sys.modules, _hachoir_mods(mock_hm)):
            result = extract_video_metadata(str(f))

        assert result["duration_seconds"] == 60.0
        assert result["resolution"] == "1280x720"
        assert result["frame_rate_fps"] == pytest.approx(25.0)
        assert result["recorded_at"] == "2024-01-15 10:30:00"

    def test_invalid_frame_rate_silently_skipped(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        mock_hm = MagicMock()
        mock_hm.has.side_effect = lambda k: k == "frame_rate"
        mock_hm.get.side_effect = lambda k: "not-a-number"

        with patch.dict(sys.modules, _hachoir_mods(mock_hm)):
            result = extract_video_metadata(str(f))

        assert "frame_rate_fps" not in result

    def test_none_parser_handled_gracefully(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        mod_parser = MagicMock()
        mod_parser.createParser.return_value = None  # createParser returns None
        mods = {"hachoir": MagicMock(), "hachoir.parser": mod_parser, "hachoir.metadata": MagicMock()}

        with patch.dict(sys.modules, mods):
            result = extract_video_metadata(str(f))

        assert isinstance(result, dict)

    def test_hachoir_import_failure_silently_skipped(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        # hachoir not present in sys.modules → ImportError → except block
        result = extract_video_metadata(str(f))
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# mutagen extraction
# ---------------------------------------------------------------------------

class TestMutagenExtraction:
    def test_gps_positive_coordinates(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        with patch.dict(sys.modules, _mutagen_mods({"\xa9xyz": ["+51.5074+000.1278/"]})):
            result = extract_video_metadata(str(f))

        assert result["latitude"] == pytest.approx(51.5074)
        assert result["longitude"] == pytest.approx(0.1278)
        assert result["location_source"] == "video"
        assert result["gps_raw"] == "+51.5074+000.1278/"

    def test_gps_negative_longitude(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        with patch.dict(sys.modules, _mutagen_mods({"\xa9xyz": ["+51.5074-000.1278/"]})):
            result = extract_video_metadata(str(f))

        assert result["latitude"] == pytest.approx(51.5074)
        assert result["longitude"] == pytest.approx(-0.1278)

    def test_gps_negative_latitude(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        with patch.dict(sys.modules, _mutagen_mods({"\xa9xyz": ["-33.8688+151.2093/"]})):
            result = extract_video_metadata(str(f))

        assert result["latitude"] == pytest.approx(-33.8688)
        assert result["longitude"] == pytest.approx(151.2093)

    def test_malformed_gps_stores_raw_but_no_lat_lon(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        with patch.dict(sys.modules, _mutagen_mods({"\xa9xyz": ["INVALID_GPS"]})):
            result = extract_video_metadata(str(f))

        assert "latitude" not in result
        assert "longitude" not in result
        assert result["gps_raw"] == "INVALID_GPS"

    def test_device_make_and_model(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        with patch.dict(sys.modules, _mutagen_mods({"\xa9mak": ["Samsung"], "\xa9mod": ["SM-G991B"]})):
            result = extract_video_metadata(str(f))

        assert result["device_make"] == "Samsung"
        assert result["device_model"] == "SM-G991B"

    def test_creation_date_from_day_tag(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        with patch.dict(sys.modules, _mutagen_mods({"\xa9day": ["2024-06-15"]})):
            result = extract_video_metadata(str(f))

        assert result["recorded_at"] == "2024-06-15"

    def test_duration_fallback_from_mutagen(self, tmp_path):
        """When hachoir is absent, duration comes from mutagen.info.length."""
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        with patch.dict(sys.modules, _mutagen_mods({}, duration=62.3)):
            result = extract_video_metadata(str(f))

        assert result["duration_seconds"] == pytest.approx(62.3, abs=0.1)

    def test_mutagen_import_failure_silently_skipped(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        # Neither hachoir nor mutagen in sys.modules → both fail silently
        result = extract_video_metadata(str(f))
        assert result == {}


# ---------------------------------------------------------------------------
# Combined / invariants
# ---------------------------------------------------------------------------

class TestInvariants:
    def test_always_returns_dict(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        result = extract_video_metadata(str(f))
        assert isinstance(result, dict)

    def test_never_raises(self, tmp_path):
        """extract_video_metadata must not raise under any circumstances."""
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")

        mod = MagicMock()
        mod.createParser.side_effect = RuntimeError("unexpected crash")
        mods = {"hachoir": MagicMock(), "hachoir.parser": mod, "hachoir.metadata": MagicMock()}

        with patch.dict(sys.modules, mods):
            result = extract_video_metadata(str(f))   # must not raise

        assert isinstance(result, dict)
