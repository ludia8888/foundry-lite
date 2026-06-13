from __future__ import annotations

from pathlib import Path

from scripts.quality import check_public_api_coverage as gate


def test_public_callable_coverage_skips_protocol_declarations(tmp_path: Path) -> None:
    module = tmp_path / "port_module.py"
    module.write_text(
        """
from typing import Protocol


def module_function() -> None:
    return None


class AdapterPort(Protocol):
    @property
    def profile_name(self) -> str: ...

    def run(self) -> None: ...


class RuntimeAdapter:
    def run(self) -> None:
        return None
""".lstrip(),
        encoding="utf-8",
    )

    names = [item.name for item in gate.collect_public_callables(module)]

    assert names == ["module_function", "RuntimeAdapter.run"]


def test_public_callable_coverage_skips_qualified_protocol_base(tmp_path: Path) -> None:
    module = tmp_path / "qualified_protocol_module.py"
    module.write_text(
        """
import typing


class RepositoryPort(typing.Protocol):
    def get(self) -> object: ...


class Repository:
    def get(self) -> object:
        return object()
""".lstrip(),
        encoding="utf-8",
    )

    names = [item.name for item in gate.collect_public_callables(module)]

    assert names == ["Repository.get"]
