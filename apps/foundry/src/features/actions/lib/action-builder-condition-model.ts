import { FOUNDRY_LITE_ACTION_CONDITION_OPERATORS } from "@foundry-lite/sdk/action-conditions";
import {
  foundryLiteActionConditionLiteralValue,
  foundryLiteActionLiteralValue,
} from "@foundry-lite/sdk/action-values";

import {
  actionBuilderConstraintsDefinition,
  actionBuilderConstraintsFromDefinition,
  emptyActionBuilderConstraints,
  hasActionBuilderConstraints,
  validateActionBuilderConstraints,
  type ActionBuilderConstraints,
} from "./action-builder-constraint-model";

export const ACTION_BUILDER_CONDITION_OPERATORS = FOUNDRY_LITE_ACTION_CONDITION_OPERATORS;

export type ActionBuilderDefaultKind =
  | "none" | "literal" | "parameter" | "objectProperty" | "currentUser" | "currentTime" | "generatedId";

export type ActionBuilderDefault = {
  kind: ActionBuilderDefaultKind;
  value: string;
  reference: string;
};

export type ActionBuilderConditionValue = {
  kind: "literal" | "parameter" | "objectProperty" | "currentUser" | "linkedObjectProperty";
  value: string;
  linkedDirection?: "outgoing" | "incoming";
  linkedProperty?: string;
  linkedAggregation?: "values" | "count";
};

export type ActionBuilderCondition =
  | {
      key: string;
      nodeType: "comparison";
      operator: string;
      left: ActionBuilderConditionValue;
      right: ActionBuilderConditionValue;
    }
  | {
      key: string;
      nodeType: "group";
      combinator: "all" | "any";
      children: ActionBuilderCondition[];
    }
  | {
      key: string;
      nodeType: "not";
      child: ActionBuilderCondition;
    };

export type ActionBuilderOverride = {
  key: string;
  condition: ActionBuilderCondition;
  required: "inherit" | "true" | "false";
  visible: "inherit" | "true" | "false";
  editable: "inherit" | "true" | "false";
  defaultValue: ActionBuilderDefault;
  isConstraintsOverridden: boolean;
  constraints: ActionBuilderConstraints;
};

export type ActionBuilderParameterPolicy = {
  apiName: string;
  dataType: string;
  constraints: ActionBuilderConstraints;
  defaultValue: ActionBuilderDefault;
  overrides: ActionBuilderOverride[];
};

export function emptyActionBuilderDefault(): ActionBuilderDefault {
  return { kind: "none", value: "", reference: "" };
}

export function newActionBuilderCondition(seed = `${Date.now()}`): ActionBuilderCondition {
  return {
    key: `condition-${seed}`,
    nodeType: "comparison",
    operator: "eq",
    left: { kind: "parameter", value: "" },
    right: { kind: "literal", value: "" },
  };
}

export function newActionBuilderOverride(index: number): ActionBuilderOverride {
  return {
    key: `override-${Date.now()}-${index}`,
    condition: newActionBuilderCondition(`override-${Date.now()}-${index}`),
    required: "inherit",
    visible: "inherit",
    editable: "inherit",
    defaultValue: emptyActionBuilderDefault(),
    isConstraintsOverridden: false,
    constraints: emptyActionBuilderConstraints(),
  };
}

export function overrideDefinition(
  override: ActionBuilderOverride,
  dataType: string,
  parameterTypes: Readonly<Record<string, string>> = {},
): Record<string, unknown> {
  const config: Record<string, unknown> = {};
  addTriState(config, "required", override.required);
  addTriState(config, "visible", override.visible);
  addTriState(config, "editable", override.editable);
  const resolvedDefault = defaultDefinition(override.defaultValue, dataType);
  if (resolvedDefault) config.default = resolvedDefault;
  if (override.isConstraintsOverridden) {
    config.constraints = actionBuilderConstraintsDefinition(override.constraints, dataType);
  }
  return { when: conditionDefinition(override.condition, parameterTypes), config };
}

export function defaultDefinition(
  value: ActionBuilderDefault,
  dataType?: string,
): Record<string, unknown> | undefined {
  if (value.kind === "none") return undefined;
  if (value.kind === "literal") {
    return { kind: "literal", value: foundryLiteActionLiteralValue(dataType, value.value) };
  }
  if (value.kind === "parameter") return { kind: "parameter", parameter: value.reference };
  if (value.kind === "objectProperty") return { kind: "objectProperty", property: value.reference };
  if (value.kind === "currentUser") return { kind: "currentUser", attribute: value.reference || "id" };
  if (value.kind === "currentTime") return { kind: "currentTime", unit: value.reference || "timestamp" };
  return { kind: "generatedId", strategy: value.reference || "uuid" };
}

export function conditionDefinition(
  condition: ActionBuilderCondition,
  parameterTypes: Readonly<Record<string, string>> = {},
): Record<string, unknown> {
  if (condition.nodeType === "group") {
    return {
      [condition.combinator]: condition.children.map((child) => conditionDefinition(child, parameterTypes)),
    };
  }
  if (condition.nodeType === "not") return { not: conditionDefinition(condition.child, parameterTypes) };
  const leftType = conditionReferenceType(condition.left, parameterTypes);
  const rightType = conditionReferenceType(condition.right, parameterTypes);
  const payload: Record<string, unknown> = {
    op: condition.operator,
    left: conditionValueDefinition(condition.left, rightType, condition.operator, true),
  };
  if (condition.operator !== "exists") {
    payload.right = conditionValueDefinition(condition.right, leftType, condition.operator, false);
  }
  return payload;
}

export function defaultFromDefinition(value: unknown): ActionBuilderDefault {
  const payload = recordValue(value);
  const kind = defaultKindValue(payload.kind);
  if (kind === "literal") return { kind, value: printableLiteral(payload.value), reference: "" };
  const reference = stringValue(
    payload.parameter ?? payload.property ?? payload.attribute ?? payload.unit ?? payload.strategy,
  );
  return { kind, value: "", reference };
}

export function overridesFromDefinition(value: unknown): ActionBuilderOverride[] {
  return arrayValue(value).map((item, index) => draftOverride(item, index));
}

export function conditionFromDefinition(value: Record<string, unknown>): ActionBuilderCondition | null {
  if (Array.isArray(value.all) || Array.isArray(value.any)) {
    const combinator = Array.isArray(value.all) ? "all" : "any";
    return {
      key: builderKey("group"),
      nodeType: "group",
      combinator,
      children: arrayValue(value[combinator]).map((child) => conditionFromDefinition(recordValue(child))).filter(isCondition),
    };
  }
  if (value.not !== undefined) {
    return {
      key: builderKey("not"),
      nodeType: "not",
      child: conditionFromDefinition(recordValue(value.not)) ?? newActionBuilderCondition("loaded-not"),
    };
  }
  if (!stringValue(value.op)) return null;
  return {
    key: builderKey("comparison"),
    nodeType: "comparison",
    operator: stringValue(value.op),
    left: conditionValueFromDefinition(value.left),
    right: conditionValueFromDefinition(value.right),
  };
}

export function validateActionBuilderParameterPolicies(parameters: ActionBuilderParameterPolicy[]): string | null {
  const available = new Set<string>();
  for (const parameter of parameters) {
    const constraintError = validateActionBuilderConstraints(parameter.constraints, parameter.dataType);
    if (constraintError) return `${parameter.apiName || "파라미터"} 제약조건: ${constraintError}`;
    const defaultError = validateDefault(parameter.defaultValue, available);
    if (defaultError) return `${parameter.apiName || "파라미터"} 기본값: ${defaultError}`;
    for (const override of parameter.overrides) {
      const conditionError = validateActionBuilderCondition(override.condition, available);
      if (conditionError) return `${parameter.apiName || "파라미터"} override: ${conditionError}`;
      const overrideDefaultError = validateDefault(override.defaultValue, available);
      if (overrideDefaultError) return `${parameter.apiName || "파라미터"} override 기본값: ${overrideDefaultError}`;
      if (override.isConstraintsOverridden) {
        const overrideConstraintError = validateActionBuilderConstraints(override.constraints, parameter.dataType);
        if (overrideConstraintError) return `${parameter.apiName || "파라미터"} override 제약조건: ${overrideConstraintError}`;
      }
      if (isEmptyOverride(override)) return `${parameter.apiName || "파라미터"} override가 바꾸는 설정이 없습니다.`;
    }
    available.add(parameter.apiName.trim());
  }
  return null;
}

export function validateActionBuilderCondition(
  condition: ActionBuilderCondition,
  available: Set<string>,
  allowLinkedObject = false,
): string | null {
  if (condition.nodeType === "group") {
    if (!condition.children.length) return "all/any 그룹은 비어 있을 수 없습니다.";
    for (const child of condition.children) {
      const childError = validateActionBuilderCondition(child, available, allowLinkedObject);
      if (childError) return childError;
    }
    return null;
  }
  if (condition.nodeType === "not") {
    if (conditionUsesGroupIdentity(condition.child)) return "그룹/역할 조건은 not으로 부정할 수 없습니다.";
    return validateActionBuilderCondition(condition.child, available, allowLinkedObject);
  }
  if (!(ACTION_BUILDER_CONDITION_OPERATORS as readonly string[]).includes(condition.operator)) {
    return `지원하지 않는 조건 연산자입니다: ${condition.operator || "(비어 있음)"}`;
  }
  if (["neq", "notIn"].includes(condition.operator) && comparisonUsesGroupIdentity(condition)) {
    return "그룹/역할 조건에는 contains, in, eq 같은 긍정 연산자를 사용하세요.";
  }
  const leftError = validateConditionValue(condition.left, available, allowLinkedObject);
  if (leftError) return leftError;
  return condition.operator === "exists" ? null : validateConditionValue(condition.right, available, allowLinkedObject);
}

function draftOverride(value: unknown, index: number): ActionBuilderOverride {
  const override = recordValue(value);
  const config = recordValue(override.config);
  return {
    key: `override-${index}-${Date.now()}`,
    condition: conditionFromDefinition(recordValue(override.when)) ?? newActionBuilderCondition(`loaded-${index}`),
    required: triStateValue(config.required),
    visible: triStateValue(config.visible),
    editable: triStateValue(config.editable),
    defaultValue: defaultFromDefinition(config.default),
    isConstraintsOverridden: Object.prototype.hasOwnProperty.call(config, "constraints"),
    constraints: actionBuilderConstraintsFromDefinition(config.constraints),
  };
}

function conditionValueDefinition(
  value: ActionBuilderConditionValue,
  peerType: string | null,
  operator: string,
  isLeft: boolean,
): Record<string, unknown> {
  if (value.kind === "literal") {
    return {
      kind: "literal",
      value: foundryLiteActionConditionLiteralValue(peerType, operator, isLeft, value.value),
    };
  }
  if (value.kind === "parameter") return { kind: "parameter", parameter: value.value };
  if (value.kind === "objectProperty") return { kind: "objectProperty", property: value.value };
  if (value.kind === "linkedObjectProperty") return {
    kind: "linkedObjectProperty",
    linkType: value.value,
    direction: value.linkedDirection ?? "outgoing",
    property: value.linkedProperty ?? "",
    aggregation: value.linkedAggregation ?? "values",
  };
  return { kind: "currentUser", attribute: value.value || "id" };
}

function conditionReferenceType(
  value: ActionBuilderConditionValue,
  parameterTypes: Readonly<Record<string, string>>,
): string | null {
  return value.kind === "parameter" ? parameterTypes[value.value] ?? null : null;
}

function conditionValueFromDefinition(value: unknown): ActionBuilderConditionValue {
  const payload = recordValue(value);
  const kind = conditionValueKind(payload.kind);
  if (kind === "literal") return { kind, value: printableLiteral(payload.value) };
  if (kind === "linkedObjectProperty") return {
    kind,
    value: stringValue(payload.linkType),
    linkedDirection: payload.direction === "incoming" ? "incoming" : "outgoing",
    linkedProperty: stringValue(payload.property),
    linkedAggregation: payload.aggregation === "count" ? "count" : "values",
  };
  return { kind, value: stringValue(payload.parameter ?? payload.property ?? payload.attribute) };
}

function validateDefault(value: ActionBuilderDefault, available: Set<string>): string | null {
  if (value.kind === "none" || value.kind === "literal") return null;
  if (!value.reference.trim()) return "참조 값을 입력하세요.";
  if (value.kind === "parameter" && !available.has(value.reference.trim())) return "앞선 파라미터만 참조할 수 있습니다.";
  return null;
}

function validateConditionValue(
  value: ActionBuilderConditionValue,
  available: Set<string>,
  allowLinkedObject: boolean,
): string | null {
  if (value.kind === "literal") return null;
  if (value.kind === "linkedObjectProperty") {
    if (!allowLinkedObject) return "연결 객체 조건은 제출 조건에서만 사용할 수 있습니다.";
    if (!value.value.trim()) return "연결 타입을 선택하세요.";
    if (!value.linkedProperty?.trim()) return "연결 객체 속성을 선택하세요.";
    return null;
  }
  if (!value.value.trim()) return "조건의 참조 값을 입력하세요.";
  if (value.kind === "parameter" && !available.has(value.value.trim())) return "선언된 파라미터만 참조할 수 있습니다.";
  return null;
}

function isEmptyOverride(override: ActionBuilderOverride): boolean {
  return [override.required, override.visible, override.editable].every((value) => value === "inherit")
    && override.defaultValue.kind === "none"
    && !override.isConstraintsOverridden
    && !hasActionBuilderConstraints(override.constraints);
}

function conditionUsesGroupIdentity(condition: ActionBuilderCondition): boolean {
  if (condition.nodeType === "group") return condition.children.some(conditionUsesGroupIdentity);
  if (condition.nodeType === "not") return conditionUsesGroupIdentity(condition.child);
  return comparisonUsesGroupIdentity(condition);
}

function comparisonUsesGroupIdentity(
  condition: Extract<ActionBuilderCondition, { nodeType: "comparison" }>,
): boolean {
  return [condition.left, condition.right].some(
    (value) => value.kind === "currentUser" && ["group", "groups", "roles"].includes(value.value || "id"),
  );
}

function addTriState(target: Record<string, unknown>, field: string, value: "inherit" | "true" | "false") {
  if (value !== "inherit") target[field] = value === "true";
}

function printableLiteral(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value) ?? "";
}

function builderKey(kind: string): string {
  return `condition-${kind}-${Date.now()}-${Math.random()}`;
}

function recordValue(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>) : {};
}

function arrayValue(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function stringValue(value: unknown): string { return typeof value === "string" ? value : ""; }
function isCondition(value: ActionBuilderCondition | null): value is ActionBuilderCondition { return value !== null; }

function defaultKindValue(value: unknown): ActionBuilderDefaultKind {
  return ["literal", "parameter", "objectProperty", "currentUser", "currentTime", "generatedId"].includes(String(value))
    ? value as ActionBuilderDefaultKind : "none";
}

function conditionValueKind(value: unknown): ActionBuilderConditionValue["kind"] {
  return value === "parameter" || value === "objectProperty" || value === "currentUser" || value === "linkedObjectProperty"
    ? value : "literal";
}

function triStateValue(value: unknown): "inherit" | "true" | "false" {
  return value === true ? "true" : value === false ? "false" : "inherit";
}
