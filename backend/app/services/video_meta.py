"""Extract technical and embedded metadata from a local video file.

Both hachoir and mutagen fail silently — metadata is best-effort.
"""

import re


def extract_video_metadata(file_path: str) -> dict:
    meta: dict = {}

    # --- Technical metadata via hachoir (resolution, duration, frame rate) ---
    try:
        from hachoir.parser import createParser
        from hachoir.metadata import extractMetadata

        parser = createParser(file_path)
        if parser:
            with parser:
                hm = extractMetadata(parser)
            if hm:
                if hm.has("duration"):
                    meta["duration_seconds"] = round(hm.get("duration").total_seconds(), 1)
                if hm.has("width") and hm.has("height"):
                    meta["resolution"] = f"{hm.get('width')}x{hm.get('height')}"
                if hm.has("frame_rate"):
                    try:
                        meta["frame_rate_fps"] = round(float(str(hm.get("frame_rate"))), 2)
                    except (ValueError, TypeError):
                        pass
                if hm.has("creation_date"):
                    meta["recorded_at"] = str(hm.get("creation_date"))
    except Exception:
        pass

    # --- Embedded tags via mutagen (GPS, device make/model, creation date) ---
    try:
        from mutagen.mp4 import MP4

        tags = MP4(file_path)

        if tags.info and "duration_seconds" not in meta:
            meta["duration_seconds"] = round(tags.info.length, 1)

        # GPS: Android/Samsung stores coordinates in the ©xyz atom.
        # Format: "+lat+lon/" or "+lat-lon/" e.g. "+51.5074-000.1278/"
        gps_raw = tags.get("\xa9xyz")  # © = \xa9
        if gps_raw:
            gps_str = str(gps_raw[0])
            meta["gps_raw"] = gps_str
            m = re.match(r"([+-]?\d+\.\d+)([+-]\d+\.\d+)", gps_str)
            if m:
                meta["latitude"] = float(m.group(1))
                meta["longitude"] = float(m.group(2))
                meta["location_source"] = "video"

        # Creation date
        day = tags.get("\xa9day")
        if day and "recorded_at" not in meta:
            meta["recorded_at"] = str(day[0])

        # Device make and model (Samsung stores these in ©mak / ©mod)
        mak = tags.get("\xa9mak")
        if mak:
            meta["device_make"] = str(mak[0])

        mod = tags.get("\xa9mod")
        if mod:
            meta["device_model"] = str(mod[0])

    except Exception:
        pass

    return meta
