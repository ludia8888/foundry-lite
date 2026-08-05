export const ACTION_BUILDER_RULE_KINDS = [
  "modifyObject",
  "createObject",
  "createOrModifyObject",
  "modifyObjects",
  "deleteObject",
  "deleteObjects",
  "createLink",
  "deleteLink",
] as const;

export const ACTION_BUILDER_VALUE_KINDS = [
  "parameter",
  "literal",
  "objectProperty",
  "priorRuleOutput",
  "currentUser",
  "currentTime",
  "generatedId",
  "webhookResponse",
] as const;

export type ActionBuilderValue = {
  kind: string;
  reference: string;
  secondary: string;
  literal: string;
};

export type ActionBuilderAssignment = {
  key: string;
  property: string;
  value: ActionBuilderValue;
};

export type ActionBuilderRule = {
  key: string;
  kind: string;
  ruleId: string;
  objectType: string;
  linkType: string;
  onInterface: string;
  interfaceLinkConstraint: string;
  target: ActionBuilderValue;
  source: ActionBuilderValue;
  primaryKey: ActionBuilderValue;
  assignments: ActionBuilderAssignment[];
};

export type ActionBuilderRuleContext = {
  parameters: string[];
  targetObjectType: string;
  targetKind: "object" | "interface";
};

export function newActionBuilderValue(kind = "parameter", reference = ""): ActionBuilderValue {
  return { kind, reference, secondary: "", literal: "" };
}

export function newActionBuilderRule(
  kind: string,
  objectType: string,
  index: number,
  targetKind: "object" | "interface" = "object",
): ActionBuilderRule {
  const isCreate = kind === "createObject";
  const onInterface = targetKind === "interface" ? objectType : "";
  return {
    key: `rule-new-${Date.now()}-${index}`,
    kind,
    ruleId: `${rulePrefix(kind)}-${index + 1}`,
    objectType,
    linkType: "",
    onInterface,
    interfaceLinkConstraint: "",
    target: newActionBuilderValue("parameter", targetKind === "interface" && isLinkRule(kind) ? "" : "__target__"),
    source: newActionBuilderValue("parameter", targetKind === "interface" && isLinkRule(kind) ? "__target__" : ""),
    primaryKey: isCreate && targetKind === "interface"
      ? newActionBuilderValue("parameter", "__target__")
      : isCreate ? newActionBuilderValue("generatedId", "uuid") : newActionBuilderValue("literal"),
    assignments: [],
  };
}

export function newActionBuilderAssignment(index: number): ActionBuilderAssignment {
  return {
    key: `assignment-new-${Date.now()}-${index}`,
    property: "",
    value: newActionBuilderValue("parameter"),
  };
}

export function actionBuilderRuleDefinition(rule: ActionBuilderRule): Record<string, unknown> {
  const base: Record<string, unknown> = { kind: rule.kind, ruleId: rule.ruleId.trim() };
  if (isLinkRule(rule.kind)) {
    return {
      ...base,
      ...(rule.interfaceLinkConstraint
        ? {
            onInterface: rule.onInterface,
            interfaceLinkConstraint: rule.interfaceLinkConstraint,
          }
        : { linkType: rule.linkType }),
      source: actionBuilderValueDefinition(rule.source),
      target: actionBuilderValueDefinition(rule.target),
    };
  }
  base.objectType = rule.objectType;
  if (rule.onInterface) base.onInterface = rule.onInterface;
  if (isCreateRule(rule.kind)) {
    base.primaryKey = actionBuilderValueDefinition(rule.primaryKey);
  } else {
    base.target = actionBuilderValueDefinition(rule.target);
  }
  if (hasAssignments(rule.kind)) base.assignments = rule.assignments.map(assignmentDefinition);
  return base;
}

export function actionBuilderRulesFromDefinition(
  raw: unknown,
  targetObjectType: string,
  targetKind: "object" | "interface" = "object",
): ActionBuilderRule[] {
  const values = Array.isArray(raw) ? raw : [];
  if (!values.length) return [newActionBuilderRule("modifyObject", targetObjectType, 0, targetKind)];
  return values.map((value, index) => ruleFromDefinition(value, index, targetObjectType));
}

export function validateActionBuilderRules(
  rules: ActionBuilderRule[],
  context: ActionBuilderRuleContext,
): string | null {
  if (!rules.length) return "최소 한 개의 객체 또는 링크 편집 규칙이 필요합니다.";
  const ids = rules.map((rule) => rule.ruleId.trim());
  if (ids.some((ruleId) => !ruleId)) return "모든 규칙에 rule ID가 필요합니다.";
  if (new Set(ids).size !== ids.length) return "rule ID는 중복될 수 없습니다.";
  const priorRules = new Set<string>();
  for (const rule of rules) {
    const error = validateRule(rule, context, priorRules);
    if (error) return `${rule.ruleId || "규칙"}: ${error}`;
    priorRules.add(rule.ruleId.trim());
  }
  return null;
}

export function isLinkRule(kind: string): boolean {
  return kind === "createLink" || kind === "deleteLink";
}

export function isCreateRule(kind: string): boolean {
  return kind === "createObject";
}

export function hasAssignments(kind: string): boolean {
  return ["createObject", "modifyObject", "modifyObjects", "createOrModifyObject"].includes(kind);
}

export function actionBuilderRuleMinimumRisk(kind: string): "low" | "medium" | "high" {
  if (["deleteObject", "deleteObjects", "modifyObjects"].includes(kind)) return "high";
  if (["createObject", "createOrModifyObject", "createLink", "deleteLink"].includes(kind)) return "medium";
  return "low";
}

function assignmentDefinition(assignment: ActionBuilderAssignment): Record<string, unknown> {
  return { property: assignment.property, value: actionBuilderValueDefinition(assignment.value) };
}

export function actionBuilderValueDefinition(value: ActionBuilderValue): Record<string, unknown> {
  if (value.kind === "literal") return { kind: "literal", value: parseLiteral(value.literal) };
  if (value.kind === "parameter") return { kind: "parameter", parameter: value.reference };
  if (value.kind === "objectProperty") {
    return { kind: "objectProperty", parameter: value.reference, property: value.secondary };
  }
  if (value.kind === "priorRuleOutput") {
    return { kind: "priorRuleOutput", ruleId: value.reference, output: value.secondary || "objectId" };
  }
  if (value.kind === "currentUser") return { kind: "currentUser", attribute: value.reference || undefined };
  if (value.kind === "currentTime") return { kind: "currentTime", unit: value.reference || "timestamp" };
  if (value.kind === "generatedId") return { kind: "generatedId", strategy: value.reference || "uuid" };
  return { kind: "webhookResponse", field: value.reference };
}

function ruleFromDefinition(value: unknown, index: number, targetObjectType: string): ActionBuilderRule {
  const payload = recordValue(value);
  const kind = stringValue(payload.kind) || "modifyObject";
  return {
    key: `rule-${index}-${stringValue(payload.ruleId)}`,
    kind,
    ruleId: stringValue(payload.ruleId) || `${rulePrefix(kind)}-${index + 1}`,
    objectType: stringValue(payload.objectType) || targetObjectType,
    linkType: stringValue(payload.linkType),
    onInterface: stringValue(payload.onInterface),
    interfaceLinkConstraint: stringValue(payload.interfaceLinkConstraint),
    target: valueFromDefinition(payload.target, "parameter", "__target__"),
    source: valueFromDefinition(payload.source),
    primaryKey: valueFromDefinition(payload.primaryKey, "generatedId", "uuid"),
    assignments: assignmentsFromDefinition(payload.assignments),
  };
}

function assignmentsFromDefinition(value: unknown): ActionBuilderAssignment[] {
  return arrayValue(value).map((item, index) => {
    const payload = recordValue(item);
    return {
      key: `assignment-${index}-${stringValue(payload.property)}`,
      property: stringValue(payload.property),
      value: valueFromDefinition(payload.value),
    };
  });
}

function valueFromDefinition(
  value: unknown,
  fallbackKind = "parameter",
  fallbackReference = "",
): ActionBuilderValue {
  const payload = recordValue(value);
  const kind = stringValue(payload.kind) || fallbackKind;
  if (kind === "literal") return { kind, reference: "", secondary: "", literal: printableLiteral(payload.value) };
  if (kind === "objectProperty") {
    return { kind, reference: stringValue(payload.parameter), secondary: stringValue(payload.property), literal: "" };
  }
  if (kind === "priorRuleOutput") {
    return { kind, reference: stringValue(payload.ruleId), secondary: stringValue(payload.output), literal: "" };
  }
  const reference = stringValue(
    payload.parameter ?? payload.attribute ?? payload.unit ?? payload.strategy ?? payload.field,
  ) || fallbackReference;
  return { kind, reference, secondary: "", literal: "" };
}

function validateRule(
  rule: ActionBuilderRule,
  context: ActionBuilderRuleContext,
  priorRules: Set<string>,
): string | null {
  if (isLinkRule(rule.kind)) {
    if (context.targetKind === "interface") {
      if (rule.onInterface !== context.targetObjectType) return "Action 대상 Interface를 지정하세요.";
      if (!rule.interfaceLinkConstraint) return "Interface Link Constraint를 선택하세요.";
    } else if (!rule.linkType) return "Link Type을 선택하세요.";
    const endpointParameters = [...context.parameters, "__target__"];
    return validateValue(rule.source, endpointParameters, priorRules)
      ?? validateValue(rule.target, endpointParameters, priorRules);
  }
  if (!rule.objectType) return "Object Type을 선택하세요.";
  if (context.targetKind === "interface") {
    if (rule.onInterface !== context.targetObjectType || rule.objectType !== context.targetObjectType) {
      return "Interface Action 규칙은 대상 Interface의 공유 계약을 사용해야 합니다.";
    }
  }
  const targetError = isCreateRule(rule.kind)
    ? validateValue(
        rule.primaryKey,
        context.targetKind === "interface" ? [...context.parameters, "__target__"] : context.parameters,
        priorRules,
      )
    : validateValue(rule.target, [...context.parameters, "__target__"], priorRules);
  if (targetError) return targetError;
  if (!hasAssignments(rule.kind)) return null;
  for (const assignment of rule.assignments) {
    if (!assignment.property) return "모든 속성 편집의 대상 속성을 선택하세요.";
    const valueError = validateValue(assignment.value, context.parameters, priorRules);
    if (valueError) return valueError;
  }
  return null;
}

function validateValue(value: ActionBuilderValue, parameters: string[], priorRules: Set<string>): string | null {
  if (value.kind === "literal") return null;
  if (!value.reference.trim()) return `${value.kind} 값의 참조가 필요합니다.`;
  if (value.kind === "parameter" && !parameters.includes(value.reference)) return "선언된 파라미터만 값으로 사용할 수 있습니다.";
  if (value.kind === "objectProperty" && (!parameters.includes(value.reference) || !value.secondary.trim())) {
    return "객체 참조 파라미터와 속성을 모두 지정하세요.";
  }
  if (value.kind === "priorRuleOutput" && !priorRules.has(value.reference)) return "앞선 규칙의 출력만 참조할 수 있습니다.";
  return null;
}

function rulePrefix(kind: string): string {
  return kind.replace(/[A-Z]/g, (match) => `-${match.toLowerCase()}`);
}

function parseLiteral(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return "";
  try { return JSON.parse(trimmed) as unknown; } catch { return value; }
}

function printableLiteral(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value) ?? "";
}

function recordValue(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>) : {};
}

function arrayValue(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function stringValue(value: unknown): string { return typeof value === "string" ? value : ""; }
