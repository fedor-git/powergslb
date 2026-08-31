# pylint: disable=function-redefined, wildcard-import, unused-wildcard-import

"""In-tree PEP 517 backend: pre-compress static assets (admin and public), then delegate to setuptools.

See https://setuptools.pypa.io/en/latest/build_meta.html
"""

import gzip
import os
from pathlib import Path
from typing import Any

import brotli
from setuptools import build_meta as _orig
from setuptools.build_meta import *  # noqa

_ADMIN_ASSET_ROOT = Path('src/powergslb/resources/admin')
_PUBLIC_ASSET_ROOT = Path('src/powergslb/resources/public')
_COMPRESSIBLE = {'.js', '.css', '.html', '.svg'}


def _compress_assets(asset_root: Path, description: str) -> None:
    """Write .gz and .br siblings next to every compressible asset in the given root.
    
    Skipped when DEVELOPMENT=true environment variable is set, allowing live editing
    of uncompressed files during development.
    
    Args:
        asset_root: Root directory containing assets to compress.
        description: Human-readable description of the asset root (for logging).
    """
    # Check if we're in DEVELOPMENT mode - skip compression for live reload
    if os.environ.get('DEVELOPMENT', '').lower() == 'true':
        print(f'DEVELOPMENT mode enabled - skipping {description} asset compression')
        return
    
    if not asset_root.exists():
        print(f'Asset root {asset_root} does not exist, skipping {description} compression')
        return
    
    for path in list(asset_root.rglob('*')):
        if path.suffix not in _COMPRESSIBLE:
            continue
        data = path.read_bytes()
        _write_if_smaller(path.parent / (path.name + '.gz'), gzip.compress(data, compresslevel=9, mtime=0), data)
        _write_if_smaller(path.parent / (path.name + '.br'), brotli.compress(data, quality=11), data)


def _compress_all_assets() -> None:
    """Compress both admin and public static assets."""
    _compress_assets(_ADMIN_ASSET_ROOT, 'admin')
    _compress_assets(_PUBLIC_ASSET_ROOT, 'public')


def _write_if_smaller(sibling: Path, encoded: bytes, original: bytes) -> None:
    """Keep the sibling only when it shrinks the asset; drop a stale one otherwise."""
    if len(encoded) < len(original):
        sibling.write_bytes(encoded)
    elif sibling.exists():
        sibling.unlink()  # drop a stale sibling from an earlier build


def build_wheel(wheel_directory: str, config_settings: dict[str, Any] | None = None,
                metadata_directory: str | None = None) -> str:
    """Pre-compress the static assets (admin and public), then build the wheel."""
    _compress_all_assets()
    return _orig.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_editable(wheel_directory: str, config_settings: dict[str, Any] | None = None,
                   metadata_directory: str | None = None) -> str:
    """Pre-compress the static assets (admin and public), then build the editable."""
    _compress_all_assets()
    return _orig.build_editable(wheel_directory, config_settings, metadata_directory)
