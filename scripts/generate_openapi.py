"""Generate docs/generated/openapi.json from FastAPI app metadata."""

from __future__ import annotations

import json
from pathlib import Path

from docgen_utils import load_app


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output_path = repo_root / "docs" / "generated" / "openapi.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    app = load_app()
    output_path.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
