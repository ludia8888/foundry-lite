from __future__ import annotations

from collections.abc import Callable

from foundry_lite.application.demo_types import SupplyChainDemoResult
from foundry_lite.application.services.demo_service import DemoService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class SupplyChainDemo:
    """Demo orchestration bounded context: seed, register, and run the supply-chain demo."""

    def __init__(self, demo: DemoService, *, reset_fresh: Callable[[], None]) -> None:
        self._demo = demo
        self._reset_fresh = reset_fresh

    def run(self, *, ctx: RequestContext | None = None, fresh: bool = False) -> SupplyChainDemoResult:
        if fresh:
            self._reset_fresh()
        return self._demo.run_supply_chain_demo(ctx=ctx)

    def register_transforms(self, ctx: RequestContext | None = None) -> None:
        self._demo.register_supply_chain_demo_transforms(ctx=ctx)

    def seed_files(self) -> None:
        self._demo.seed_supply_chain_demo_files()
