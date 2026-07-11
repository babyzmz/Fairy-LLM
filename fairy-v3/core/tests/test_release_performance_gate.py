from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT))

from scripts.release_performance import measure_initial_gzip  # noqa: E402


def test_initial_gzip_follows_manifest_static_and_dynamic_dependencies(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    manifest_dir = tmp_path / ".vite"
    manifest_dir.mkdir()
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/assets/main.js"></script>'
        '<link rel="stylesheet" href="/assets/main.css">',
        encoding="utf-8",
    )
    main = b'import{value}from"./shared.js";import("./lazy.js");console.log(value);'
    shared = b'export const value="shared";'
    lazy = b'export const lazy="must not count";'
    css = b"body{color:#18201d}"
    (assets / "main.js").write_bytes(main)
    (assets / "shared.js").write_bytes(shared)
    (assets / "lazy.js").write_bytes(lazy)
    (assets / "main.css").write_bytes(css)
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "index.html": {
                    "file": "assets/main.js",
                    "isEntry": True,
                    "imports": ["shared"],
                    "dynamicImports": ["lazy"],
                    "css": ["assets/main.css"],
                },
                "shared": {"file": "assets/shared.js"},
                "lazy": {"file": "assets/lazy.js"},
            }
        ),
        encoding="utf-8",
    )

    result = measure_initial_gzip(tmp_path)

    assert result.files == (
        Path("assets/lazy.js"),
        Path("assets/main.css"),
        Path("assets/main.js"),
        Path("assets/shared.js"),
    )
    assert result.gzip_bytes == sum(
        len(gzip.compress(payload, compresslevel=9, mtime=0))
        for payload in (css, main, lazy, shared)
    )


def test_initial_gzip_rejects_entry_paths_outside_dist(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<script type="module" src="/../secret.js"></script>',
        encoding="utf-8",
    )

    try:
        measure_initial_gzip(tmp_path)
    except ValueError as error:
        assert "outside" in str(error)
    else:
        raise AssertionError("out-of-scope bundle path was accepted")
