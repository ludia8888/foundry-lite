from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from examples.restaurant_reservation_os.publish_policy_ontology import (
    RESERVATION_PROPERTIES,
    RESTAURANT_PROPERTIES,
    TABLE_PROPERTIES,
    _add_policy_resources,
)
from scripts.generate_consumer_osdk import DEFAULT_INVENTORY, generate

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "packages" / "restaurant-reservation-osdk" / "osdk.contract.json"
GENERATED = ROOT / "packages" / "restaurant-reservation-osdk" / "src" / "generated.ts"


def test_restaurant_consumer_osdk_matches_executable_policy_resources() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    objects = {item["apiName"]: item for item in contract["objects"]}
    definition = {
        "objectTypes": [{"apiName": name, "properties": []} for name in ("Restaurant", "DiningTable", "Reservation")],
        "functionTypes": [],
        "actionTypes": [],
    }
    _add_policy_resources(definition)
    functions = {item["apiName"]: item for item in definition["functionTypes"]}
    actions = {item["apiName"]: item for item in definition["actionTypes"]}
    contract_functions = {item["apiName"]: item for item in contract["functions"]}
    contract_actions = {item["apiName"]: item for item in contract["actions"]}

    assert _names(objects["Restaurant"]["properties"]) >= _spec_names(RESTAURANT_PROPERTIES)
    assert _names(objects["DiningTable"]["properties"]) >= _spec_names(TABLE_PROPERTIES)
    assert _names(objects["Reservation"]["properties"]) >= _spec_names(RESERVATION_PROPERTIES)
    assert _names(contract_functions["QuoteAtomicReservationPolicy"]["inputs"]) == _api_names(
        functions["QuoteAtomicReservationPolicy"]["inputs"]
    )
    for action_name in (
        "ReserveTableWithAtomicInventory",
        "PayReservationDeposit",
        "CancelCustomerReservation",
    ):
        assert _names(contract_actions[action_name]["parameters"]) == _api_names(actions[action_name]["parameters"])


def test_restaurant_consumer_osdk_generated_artifact_is_fresh_and_high_level() -> None:
    assert generate(DEFAULT_INVENTORY, is_check=True) == []
    source = GENERATED.read_text(encoding="utf-8")

    assert "consumer_osdk_strict" in source
    assert "OsdkObjectType<Restaurant>" in source
    assert "OsdkFunctionType<QuoteAtomicReservationPolicyInputs, ReservationPolicyQuote>" in source
    assert "OsdkActionType<ReserveTableWithAtomicInventoryRequest" in source
    assert "useFoundryLiteClient" not in source
    assert ".objects.generic" not in source
    assert ".functions.generic" not in source
    assert ".actions.runs" not in source


def _names(values: object) -> set[str]:
    return {str(item["name"]) for item in cast(list[dict[str, object]], values)}


def _api_names(values: object) -> set[str]:
    return {str(item["apiName"]) for item in cast(list[dict[str, object]], values)}


def _spec_names(values: list[tuple[str, str, str, bool]]) -> set[str]:
    return {item[0] for item in values}
