"""Generate endpoint-to-test coverage docs from FastAPI routes and pytest files."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.routing import APIRoute

from docgen_utils import load_app


def _load_app_routes() -> list[tuple[str, str, str]]:
    app = load_app()

    routes: list[tuple[str, str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.include_in_schema:
            continue
        methods = sorted(m for m in (route.methods or set()) if m not in {"HEAD", "OPTIONS"})
        for method in methods:
            routes.append((method, route.path, route.name))
    return sorted(routes, key=lambda item: (item[1], item[0]))


def _route_pattern(path_template: str) -> re.Pattern[str]:
    escaped = re.escape(path_template)
    escaped = re.sub(r"\\\\\{[^}]+\\\\\}", r"[^/]+", escaped)
    return re.compile(rf"^{escaped}$")


def _extract_called_paths(test_text: str, method: str) -> list[str]:
    call_re = re.compile(rf"\b{method.lower()}\s*\(\s*f?[\"\']([^\"\']+)[\"\']", re.IGNORECASE)
    return [match.group(1).split("?", 1)[0] for match in call_re.finditer(test_text)]


def _find_matching_tests(routes: list[tuple[str, str, str]], tests_dir: Path) -> dict[tuple[str, str], list[str]]:
    mapping: dict[tuple[str, str], list[str]] = {(method, path): [] for method, path, _ in routes}

    test_files = sorted(tests_dir.glob("test_*.py"))
    parsed_calls: dict[Path, dict[str, list[str]]] = {}
    for file_path in test_files:
        text = file_path.read_text(encoding="utf-8")
        parsed_calls[file_path] = {
            "get": _extract_called_paths(text, "get"),
            "post": _extract_called_paths(text, "post"),
            "patch": _extract_called_paths(text, "patch"),
            "put": _extract_called_paths(text, "put"),
            "delete": _extract_called_paths(text, "delete"),
        }

    for method, path, _name in routes:
        pattern = _route_pattern(path)
        key = (method, path)
        calls_key = method.lower()
        matches: list[str] = []
        for file_path in test_files:
            called_paths = parsed_calls[file_path].get(calls_key, [])
            if any(pattern.match(candidate) for candidate in called_paths):
                matches.append(file_path.name)
        mapping[key] = matches

    return mapping


def _write_markdown(
    output_path: Path,
    routes: list[tuple[str, str, str]],
    tests_by_route: dict[tuple[str, str], list[str]],
) -> None:
    lines: list[str] = []
    lines.append("# API Feature Matrix (Generated)")
    lines.append("")
    lines.append("Source of truth: FastAPI router table plus discovered pytest call sites.")
    lines.append("")
    lines.append("| Method | Path | Handler | Matching test files |")
    lines.append("|---|---|---|---|")

    for method, path, name in routes:
        matches = tests_by_route.get((method, path), [])
        match_text = ", ".join(matches) if matches else "(none found)"
        lines.append(f"| {method} | `{path}` | `{name}` | {match_text} |")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    tests_dir = repo_root / "backend" / "tests"
    output_path = repo_root / "docs" / "generated" / "feature-matrix.md"

    routes = _load_app_routes()
    tests_by_route = _find_matching_tests(routes, tests_dir)
    _write_markdown(output_path, routes, tests_by_route)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
