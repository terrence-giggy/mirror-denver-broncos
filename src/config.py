"""Project configuration management."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Optional

from src import paths

class ProjectConfig:
    """Access to project configuration values."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        
        config_path = paths.get_config_file()
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                # If config fails to load, we treat it as empty
                self._data = {}
        
        self._loaded = True

    @property
    def model(self) -> str:
        """Get the configured LLM model, defaulting to gpt-4o-mini."""
        self._ensure_loaded()
        return self._data.get("model", "gpt-4o-mini")

    @property
    def source_url(self) -> Optional[str]:
        """Get the configured source URL."""
        self._ensure_loaded()
        return self._data.get("source_url")

    @property
    def topic(self) -> Optional[str]:
        """Get the configured topic."""
        self._ensure_loaded()
        return self._data.get("topic")

    @property
    def frequency(self) -> Optional[str]:
        """Get the configured update frequency."""
        self._ensure_loaded()
        return self._data.get("frequency")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a raw configuration value."""
        self._ensure_loaded()
        return self._data.get(key, default)

@lru_cache(maxsize=1)
def get_config() -> ProjectConfig:
    """Get the singleton configuration instance."""
    return ProjectConfig()
