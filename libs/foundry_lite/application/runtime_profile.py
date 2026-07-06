"""Runtime profile value object for core dependency composition."""

from __future__ import annotations

from dataclasses import dataclass

_LOCAL_RUNTIME_PROFILES = frozenset({"local", "dev", "development", "demo", "test"})
_PROTECTED_RUNTIME_PROFILES = frozenset({"production", "prod", "staging", "stage"})
_KNOWN_RUNTIME_PROFILES = _LOCAL_RUNTIME_PROFILES | _PROTECTED_RUNTIME_PROFILES


@dataclass(frozen=True)
class RuntimeProfile:
    name: str = "local"

    @classmethod
    def from_value(cls, value: str | RuntimeProfile | None) -> RuntimeProfile:
        if isinstance(value, RuntimeProfile):
            return value
        normalized = (value or "local").strip().lower().replace("_", "-")
        if not normalized:
            normalized = "local"
        if normalized not in _KNOWN_RUNTIME_PROFILES:
            raise ValueError(
                f"unknown FOUNDRY_LITE_RUNTIME_PROFILE: {normalized}; choose local/demo/test/staging/production"
            )
        return cls(normalized)

    @property
    def is_local_like(self) -> bool:
        return self.name in _LOCAL_RUNTIME_PROFILES

    @property
    def is_protected(self) -> bool:
        return self.name in _PROTECTED_RUNTIME_PROFILES
