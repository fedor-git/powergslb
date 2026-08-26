# pylint: disable=function-redefined, wildcard-import, unused-wildcard-import

"""In-tree PEP 517 backend: pre-compress admin static assets, then delegate to setuptools.

See https://setuptools.pypa.io/en/latest/build_meta.html
"""

import gzip
import os
from pathlib import Path
from typing import Any

import brotli
from setuptools import build_meta as _orig
from setuptools.build_meta import *  # noqa

_ASSET_ROOT = Path('src/powergslb/resources/admin')
_COMPRESSIBLE = {'.js', '.css', '.html', '.svg'}


def _compress_admin_assets() -> None:
    """Write a .gz and .br sibling next to every compressible admin asset.
    
    Skipped when DEVELOPMENT=true environment variable is set, allowing live editing
    of uncompressed files during development.
    """
    # Check if we're in DEVELOPMENT mode - skip compression for live reload
    if os.environ.get('DEVELOPMENT', '').lower() == 'true':
        print('DEVELOPMENT mode enabled - skipping admin asset compression')
        return
    
    for path in list(_ASSET_ROOT.rglob('*')):
        if path.suffix not in _COMPRESSIBLE:
            continue
        data = path.read_bytes()
        _write_if_smaller(path.parent / (path.name + '.gz'), gzip.compress(data, compresslevel=9, mtime=0), data)
        _write_if_smaller(path.parent / (path.name + '.br'), brotli.compress(data, quality=11), data)


def _write_if_smaller(sibling: Path, encoded: bytes, original: bytes) -> None:
    """Keep the sibling only when it shrinks the asset; drop a stale one otherwise."""
    if len(encoded) < len(original):
        sibling.write_bytes(encoded)
    elif sibling.exists():
        sibling.unlink()  # drop a stale sibling from an earlier build


def build_wheel(wheel_directory: str, config_settings: dict[str, Any] | None = None,
                metadata_directory: str | None = None) -> str:
    """Pre-compress the admin assets, then build the wheel."""
    _compress_admin_assets()
    return _orig.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_editable(wheel_directory: str, config_settings: dict[str, Any] | None = None,
                   metadata_directory: str | None = None) -> str:
    """Pre-compress the admin assets, then build the editable."""
    _compress_admin_assets()
    return _orig.build_editable(wheel_directory, config_settings, metadata_directory)
