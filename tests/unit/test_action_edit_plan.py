"""EditPlan structural validation and IR->plan building against a fake context."""

from __future__ import annotations

import pytest
from foundry_lite.domain.action_runtime.action_ir import (
    ActionDefinitionV2,
    CreateLinkRule,
    CreateObjectRule,
    FunctionEditRule,
    ModifyObjectRule,
    PropertyAssignment,
)
from foundry_lite.domain.action_runtime.edit_plan import (
    EditPlan,
    LinkCreate,
    ObjectCreate,
    ObjectDelete,
    ObjectModify,
    ObjectRef,
    validate_edit_plan,
)
from foundry_lite.domain.action_runtime.edit_plan_builder import build_edit_plan
from foundry_lite.domain.action_runtime.value_expression import (
    GeneratedIdValue,
    LiteralValue,
    ParameterValue,
    PriorRuleOutputValue,
    ValueExpression,
    evaluate_value,
)
from foundry_lite.domain.errors import ValidationFailed


class _FakeContext:
    """Resolves params and object references from static fixtures."""

    def __init__(
        self, params: dict[str, object], objects: dict[str, ObjectRef], sets: dict[str, tuple[ObjectRef, ...]]
    ):
        self._params = params
        self._objects = objects
        self._sets = sets
        self._gen = 0

    def parameter(self, name: str) -> object:
        return self._params.get(name)

    def object_property(self, parameter: str, prop: str) -> object:
        return f"{parameter}.{prop}"

    def prior_rule_output(self, rule_id: str, output: str) -> object:
        return f"{rule_id}#{output}"

    def function_output(self, key: str) -> object:
        return key

    def current_user(self, attribute: str | None) -> object:
        return "user-1"

    def current_time(self, unit: str) -> str:
        return f"now:{unit}"

    def generated_id(self, strategy: str) -> str:
        return f"gen:{strategy}"

    def webhook_response(self, field: str) -> object:
        return field

    def evaluate(self, expression: ValueExpression) -> object:
        return evaluate_value(expression, self)

    def resolve_existing_object(self, object_type: str, expression: ValueExpression) -> ObjectRef:
        name = expression.parameter if isinstance(expression, ParameterValue) else "?"
        ref = self._objects.get(name)
        if ref is None or ref.object_type != object_type:
            raise ValidationFailed("no such object", details={"parameter": name, "objectType": object_type})
        return ref

    def resolve_existing_object_set(self, object_type: str, expression: ValueExpression) -> tuple[ObjectRef, ...]:
        name = expression.parameter if isinstance(expression, ParameterValue) else "?"
        return tuple(ref for ref in self._sets.get(name, ()) if ref.object_type == object_type)

    def resolve_link_endpoint(self, link_type: str, role: str, expression: ValueExpression) -> str:
        del link_type, role
        return str(self.evaluate(expression))

    def generate_object_id(self, rule_id: str) -> str:
        self._gen += 1
        return f"new-{rule_id}-{self._gen}"

    def operation_key(self, rule_id: str, discriminator: str) -> str:
        return f"idem:{rule_id}:{discriminator}"


def test_build_fulfill_order_plan_covers_create_modify_and_link() -> None:
    definition = ActionDefinitionV2(
        api_name="FulfillOrder",
        rules=(
            ModifyObjectRule(
                rule_id="close-order",
                object_type="Order",
                target=ParameterValue("order"),
                assignments=(PropertyAssignment("status", LiteralValue("FULFILLED")),),
            ),
            CreateObjectRule(
                rule_id="mk-shipment",
                object_type="Shipment",
                primary_key=GeneratedIdValue("uuid"),
                assignments=(PropertyAssignment("carrier", ParameterValue("carrier")),),
            ),
            CreateLinkRule(
                rule_id="link",
                link_type="OrderShipment",
                source=ParameterValue("order"),
                target=PriorRuleOutputValue("mk-shipment", "objectId"),
            ),
        ),
    )
    context = _FakeContext(
        params={"carrier": "UPS", "order": "O-1001"},
        objects={"order": ObjectRef("Order", "O-1001", 7)},
        sets={},
    )
    plan = build_edit_plan(definition, context)
    validate_edit_plan(plan)

    assert [c.object_type for c in plan.objects_to_create] == ["Shipment"]
    created = plan.objects_to_create[0]
    assert created.primary_key == "new-mk-shipment-1"
    assert created.properties == {"carrier": "UPS"}
    assert [m.object_id for m in plan.objects_to_modify] == ["O-1001"]
    assert plan.objects_to_modify[0].expected_version == 7
    assert plan.objects_to_modify[0].patch == {"status": "FULFILLED"}
    assert plan.read_set_versions == {"O-1001": 7}
    link = plan.links_to_create[0]
    assert (link.source_object_id, link.target_object_id) == ("O-1001", "new-mk-shipment-1")


def test_modify_objects_many_fans_out_over_the_object_set() -> None:
    definition = ActionDefinitionV2(
        api_name="BulkClose",
        rules=(
            ModifyObjectRule(
                rule_id="bulk",
                object_type="Order",
                target=ParameterValue("orders"),
                assignments=(PropertyAssignment("status", LiteralValue("CLOSED")),),
                cardinality="many",
            ),
        ),
    )
    context = _FakeContext(
        params={},
        objects={},
        sets={"orders": (ObjectRef("Order", "O-1", 2), ObjectRef("Order", "O-2", 5))},
    )
    plan = build_edit_plan(definition, context)
    validate_edit_plan(plan)
    assert {m.object_id: m.expected_version for m in plan.objects_to_modify} == {"O-1": 2, "O-2": 5}
    assert plan.read_set_versions == {"O-1": 2, "O-2": 5}


def test_literal_primary_key_is_used_as_created_object_id() -> None:
    definition = ActionDefinitionV2(
        api_name="Create",
        rules=(
            CreateObjectRule(
                rule_id="c",
                object_type="Order",
                primary_key=LiteralValue("O-9"),
                assignments=(),
            ),
        ),
    )
    plan = build_edit_plan(definition, _FakeContext({}, {}, {}))
    assert plan.objects_to_create[0].primary_key == "O-9"


def test_link_to_uncreated_object_is_rejected_at_build() -> None:
    definition = ActionDefinitionV2(
        api_name="Bad",
        rules=(
            CreateLinkRule(
                rule_id="link",
                link_type="T",
                source=ParameterValue("a"),
                target=PriorRuleOutputValue("missing", "objectId"),
            ),
        ),
    )
    context = _FakeContext({}, {"a": ObjectRef("A", "A-1", 1)}, {})
    with pytest.raises(ValidationFailed, match="not created in this action"):
        build_edit_plan(definition, context)


def test_function_rule_is_not_handled_by_the_rule_planner() -> None:
    definition = ActionDefinitionV2(api_name="Fn", rules=(FunctionEditRule("fn", "computeEdits", "1.0"),))
    with pytest.raises(ValidationFailed, match="function edit adapter"):
        build_edit_plan(definition, _FakeContext({}, {}, {}))


def test_validate_rejects_modify_and_delete_of_same_object() -> None:
    plan = EditPlan(
        objects_to_modify=(ObjectModify("k1", "r", "Order", "O-1", 1, {"a": 1}),),
        objects_to_delete=(ObjectDelete("k2", "r", "Order", "O-1", 1),),
    )
    with pytest.raises(ValidationFailed, match="modifies and deletes the same object"):
        validate_edit_plan(plan)


def test_validate_rejects_duplicate_operation_keys() -> None:
    plan = EditPlan(
        objects_to_create=(ObjectCreate("dup", "r1", "Order", "O-1", {}),),
        objects_to_modify=(ObjectModify("dup", "r2", "Order", "O-2", 1, {}),),
    )
    with pytest.raises(ValidationFailed, match="duplicate operation keys"):
        validate_edit_plan(plan)


def test_validate_rejects_link_to_deleted_object() -> None:
    plan = EditPlan(
        objects_to_delete=(ObjectDelete("k1", "r", "Order", "O-1", 3),),
        links_to_create=(LinkCreate("k2", "r", "T", "O-1", "O-2"),),
    )
    with pytest.raises(ValidationFailed, match="link to an object it deletes"):
        validate_edit_plan(plan)


def test_validate_rejects_empty_plan() -> None:
    with pytest.raises(ValidationFailed, match="empty"):
        validate_edit_plan(EditPlan())


def test_validate_allows_same_id_across_different_object_types() -> None:
    # Regression: object ids are unique only per type, so modifying Order "X-1" and
    # deleting Customer "X-1" is two distinct objects, not a modify/delete conflict.
    plan = EditPlan(
        objects_to_modify=(ObjectModify("k1", "r", "Order", "X-1", 1, {"a": 1}),),
        objects_to_delete=(ObjectDelete("k2", "r", "Customer", "X-1", 1),),
    )
    validate_edit_plan(plan)


def test_build_rejects_empty_explicit_primary_key() -> None:
    # Regression: an explicit (non-generated) primary key that resolves empty must fail,
    # not silently mint a random id that diverges from the declared natural key.
    definition = ActionDefinitionV2(
        api_name="Create",
        rules=(CreateObjectRule(rule_id="c", object_type="Order", primary_key=LiteralValue(""), assignments=()),),
    )
    with pytest.raises(ValidationFailed, match="primary key resolved to an empty value"):
        build_edit_plan(definition, _FakeContext({}, {}, {}))
