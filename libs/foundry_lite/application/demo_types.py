"""Application-layer models and helpers for demo types."""

from __future__ import annotations

from typing import TypedDict

from foundry_lite.application.action_types import ActionApplyResponse
from foundry_lite.application.ports import ObjectIndexRebuildResult, ObjectPayload, OntologyApplyResult


class SupplyChainDemoResult(TypedDict):
    rawOrdersVersion: str
    rawCustomersVersion: str
    cleanOrdersVersion: str
    cleanOrderFinanceVersion: str
    cleanCustomersVersion: str
    ontology: OntologyApplyResult
    orderIndex: ObjectIndexRebuildResult
    customerIndex: ObjectIndexRebuildResult
    action: ActionApplyResponse
    actionLogVersion: str
    orderCurrentVersion: str
    customerRiskVersion: str
    customerReindex: ObjectIndexRebuildResult
    customer: ObjectPayload
