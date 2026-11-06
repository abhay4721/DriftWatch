"""Canonical value normalization used by the comparison engine."""

import unicodedata
from collections.abc import Mapping
from typing import Any


def normalize(value: Any) -> Any:
    """Sort object keys and normalize Unicode while preserving meaningful strings."""
    if isinstance(value, Mapping):
        return {str(key): normalize(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    return value
