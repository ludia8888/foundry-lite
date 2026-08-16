import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";
import type { OntologyBranchActionType, OntologyCatalogInterface, OntologyCatalogLink } from "@foundry-lite/sdk";

import {
  conditionDefinition,
  conditionFromDefinition,
  defaultDefinition,
  defaultFromDefinition,
  emptyActionBuilderDefault,
  overrideDefinition,
  overridesFromDefinition,
  validateActionBuilderCondition,
  validateActionBuilderParameterPolicies,
  type ActionBuilderCondition,
  type ActionBuilderDefault,
  type ActionBuilderOverride,
} from "./action-builder-condition-model";
import {
  actionBuilderConstraintsDefinition,
  actionBuilderConstraintsFromDefinition,
  emptyActionBuilderConstraints,
  type ActionBuilderConstraints,
} from "./action-builder-constraint-model";
import {
  actionBuilderEffectDefinition,
  actionBuilderEffectsFromDefinition,
  validateActionBuilderEffects,
  type ActionBuilderEffect,
} from "./action-builder-effect-model";
import {
  actionBuilderRuleDefinition,
  actionBuilderRuleMinimumRisk,
  actionBuilderRulesFromDefinition,
  validateActionBuilderRules,
  type ActionBuilderRule,
} from "./action-builder-rule-model";

export {
  ACTION_BUILDER_CONDITION_OPERATORS,
  emptyActionBuilderDefault,
  newActionBuilderCondition,
  newActionBuilderOverride,
  type ActionBuilderCondition,
  type ActionBuilderConditionValue,
  type ActionBuilderDefault,
  type ActionBuilderDefaultKind,
  type ActionBuilderOverride,
} from "./action-builder-condition-model";
export {
  ACTION_BUILDER_EFFECT_KINDS,
  newActionBuilderEffect,
  newActionBuilderEffectPayloadEntry,
  newActionBuilderEffectResponseField,
  type ActionBuilderEffect,
  type ActionBuilderEffectPayloadEntry,
  type ActionBuilderEffectResponseField,
} from "./action-builder-effect-model";
export {
  ACTION_BUILDER_RULE_KINDS,
  ACTION_BUILDER_VALUE_KINDS,
  actionBuilderRuleMinimumRisk,
  hasAssignments,
  isCreateRule,
  isLinkRule,
  newActionBuilderAssignment,
  newActionBuilderRule,
  newActionBuilderValue,
  type ActionBuilderAssignment,
  type ActionBuilderRule,
  type ActionBuilderValue,
} from "./action-builder-rule-model";

export const ACTION_BUILDER_PARAMETER_TYPES = [
  "string", "boolean", "integer", "long", "float", "decimal", "date", "timestamp",
  "object", "interface", "objectSet", "array", "struct", "media", "attachment",
] as const;

export type ActionBuilderStructField = {
  key: string;
  apiName: string;
  dataType: string;
  description: string;
  isRequired: boolean;
  mediaSet: string;
  allowedMimeTypes: string;
  maxBytes: string;
  render: "filePicker" | "textInput";
  fields: ActionBuilderStructField[];
};

export type ActionBuilderParameter = {
  key: string;
  apiName: string;
  dataType: string;
  description: string;
  isRequired: boolean;
  referenceType: string;
  itemType: string;
  mediaSet: string;
  allowedMimeTypes: string;
  maxBytes: string;
  render: "filePicker" | "textInput";
  fields: ActionBuilderStructField[];
  constraints: ActionBuilderConstraints;
  defaultValue: ActionBuilderDefault;
  overrides: ActionBuilderOverride[];
};

export type ActionBuilderFormSection = {
  key: string;
  id: string;
  title: string;
  description: string;
  columns: 1 | 2;
  isCollapsible: boolean;
  isInitiallyCollapsed: boolean;
  parameterKeys: string[];
  visibleWhen: ActionBuilderCondition | null;
};

export type ActionBuilderDraft = {
  apiName: string;
  displayName: string;
  description: string;
  target: string;
  targetKind: "object" | "interface";
  riskLevel: "low" | "medium" | "high";
  agentExecutionPolicy: "plan_only" | "approval_required" | "autonomous";
  agentToolDescription: string;
  viewRoles: string;
  editRoles: string;
  applyRoles: string;
  sections: ActionBuilderFormSection[];
  parameters: ActionBuilderParameter[];
  rules: ActionBuilderRule[];
  executionMode: "rules" | "function";
  functionApiName: string;
  functionVersion: string;
  functionExecutionMode: "per_request" | "batched";
  functionBatchInputName: string;
  functionMaxBatchSize: number;
  effects: ActionBuilderEffect[];
  isRevertEnabled: boolean;
  compensationActionApiName: string;
  submissionCriteria: ActionBuilderCondition | null;
  submissionMessage: string;
};

export function newActionBuilderParameter(index: number): ActionBuilderParameter {
  return {
    key: `parameter-new-${Date.now()}-${index}`,
    apiName: "",
    dataType: "string",
    description: "",
    isRequired: false,
    referenceType: "",
    itemType: "string",
    mediaSet: "",
    allowedMimeTypes: "",
    maxBytes: "",
    render: "filePicker",
    fields: [],
    constraints: emptyActionBuilderConstraints(),
    defaultValue: emptyActionBuilderDefault(),
    overrides: [],
  };
}

export function newActionBuilderStructField(index: number): ActionBuilderStructField {
  return {
    key: `struct-field-${Date.now()}-${index}`,
    apiName: "",
    dataType: "string",
    description: "",
    isRequired: false,
    mediaSet: "",
    allowedMimeTypes: "",
    maxBytes: "",
    render: "filePicker",
    fields: [],
  };
}

export function newActionBuilderFormSection(index: number): ActionBuilderFormSection {
  return {
    key: `form-section-${Date.now()}-${index}`,
    id: index === 0 ? "primary" : `section-${index + 1}`,
    title: index === 0 ? "Parameters" : `Section ${index + 1}`,
    description: "",
    columns: 1,
    isCollapsible: false,
    isInitiallyCollapsed: false,
    parameterKeys: [],
    visibleWhen: null,
  };
}

export function emptyActionBuilderDraft(target = ""): ActionBuilderDraft {
  return {
    apiName: "",
    displayName: "",
    description: "",
    target,
    targetKind: "object",
    riskLevel: "high",
    agentExecutionPolicy: "approval_required",
    agentToolDescription: "",
    viewRoles: "viewer, ops_manager, data_engineer",
    editRoles: "data_engineer",
    applyRoles: "ops_manager",
    sections: [newActionBuilderFormSection(0)],
    parameters: [],
    rules: actionBuilderRulesFromDefinition([], target),
    executionMode: "rules",
    functionApiName: "",
    functionVersion: "",
    functionExecutionMode: "per_request",
    functionBatchInputName: "",
    functionMaxBatchSize: 20,
    effects: [],
    isRevertEnabled: false,
    compensationActionApiName: "",
    submissionCriteria: null,
    submissionMessage: "",
  };
}

export function actionBuilderDefinition(draft: ActionBuilderDraft): Record<string, unknown> {
  const parameterTypes = Object.fromEntries(
    draft.parameters.map((parameter) => [parameter.apiName.trim(), parameter.dataType]),
  );
  return removeUndefined({
    contractVersion: 3,
    apiName: draft.apiName.trim(),
    displayName: draft.displayName.trim() || draft.apiName.trim(),
    description: draft.description.trim() || undefined,
    target: draft.target,
    targetKind: draft.targetKind,
    riskLevel: draft.riskLevel,
    agentExecutionPolicy: draft.agentExecutionPolicy,
    agentToolDescription: draft.agentToolDescription.trim() || undefined,
    permissions: {
      viewRoles: commaSeparatedValues(draft.viewRoles),
      editRoles: commaSeparatedValues(draft.editRoles),
      applyRoles: commaSeparatedValues(draft.applyRoles),
    },
    parameters: draft.parameters.map((parameter) => parameterDefinition(parameter, parameterTypes)),
    submissionCriteria: submissionCriteriaDefinition(draft, parameterTypes),
    formLayout: formLayoutDefinition(draft, parameterTypes),
    rules: draft.executionMode === "rules" ? draft.rules.map(actionBuilderRuleDefinition) : undefined,
    function: draft.executionMode === "function" ? {
      apiName: draft.functionApiName.trim(),
      version: draft.functionVersion.trim(),
      executionMode: draft.functionExecutionMode,
      batchInputName: draft.functionExecutionMode === "batched"
        ? draft.functionBatchInputName.trim()
        : undefined,
      maxBatchSize: draft.functionMaxBatchSize,
    } : undefined,
    effects: draft.effects.map(actionBuilderEffectDefinition),
    actionLog: { enabled: true },
    revert: {
      enabled: draft.isRevertEnabled,
      compensationAction: draft.compensationActionApiName.trim() || undefined,
    },
    branchPolicy: { allowExternalEffects: false },
  });
}

export function actionBuilderDraftFromItem(item: OntologyBranchActionType): ActionBuilderDraft {
  const definition = item.definition;
  const formLayout = recordValue(definition.formLayout);
  const permissions = recordValue(definition.permissions);
  const criteria = recordValue(definition.submissionCriteria);
  const parameters = arrayValue(definition.parameters).map((value, index) => draftParameter(value, index));
  const targetKind = targetKindValue(definition.targetKind);
  return {
    apiName: stringValue(definition.apiName),
    displayName: stringValue(definition.displayName),
    description: stringValue(definition.description),
    target: targetName(definition.target),
    targetKind,
    riskLevel: riskLevelValue(definition.riskLevel),
    agentExecutionPolicy: agentPolicyValue(definition.agentExecutionPolicy),
    agentToolDescription: stringValue(definition.agentToolDescription),
    viewRoles: arrayValue(permissions.viewRoles).filter(isString).join(", ") || "viewer, ops_manager, data_engineer",
    editRoles: arrayValue(permissions.editRoles).filter(isString).join(", ") || "data_engineer",
    applyRoles: arrayValue(permissions.applyRoles ?? permissions.allowedRoles).filter(isString).join(", "),
    sections: draftSections(formLayout.sections, parameters),
    parameters,
    rules: actionBuilderRulesFromDefinition(definition.rules, targetName(definition.target), targetKind),
    executionMode: definition.function ? "function" : "rules",
    functionApiName: stringValue(recordValue(definition.function).apiName),
    functionVersion: stringValue(recordValue(definition.function).version),
    functionExecutionMode: functionExecutionModeValue(recordValue(definition.function).executionMode),
    functionBatchInputName: stringValue(recordValue(definition.function).batchInputName),
    functionMaxBatchSize: functionBatchSizeValue(recordValue(definition.function)),
    effects: actionBuilderEffectsFromDefinition(definition.effects),
    isRevertEnabled: recordValue(definition.revert).enabled === true,
    compensationActionApiName: stringValue(recordValue(definition.revert).compensationAction),
    submissionCriteria: conditionFromDefinition(criteria),
    submissionMessage: stringValue(criteria.message),
  };
}

export function objectProperties(
  objects: FoundryLiteOntologyObjectView[],
  target: string,
): FoundryLiteOntologyObjectView["properties"] {
  return objects.find((object) => object.apiName === target)?.properties ?? [];
}

export type ActionBuilderPropertyOption = {
  apiName: string;
  displayName: string;
  dataType: string;
};

export type ActionBuilderLinkedPropertyOption = {
  key: string;
  linkType: string;
  direction: "outgoing" | "incoming";
  objectType: string;
  property: string;
  label: string;
};

export function interfaceProperties(
  interfaces: OntologyCatalogInterface[],
  target: string,
): ActionBuilderPropertyOption[] {
  const properties = interfaces.find((item) => item.apiName === target)?.properties ?? [];
  return properties.flatMap((value) => {
    const apiName = stringValue(value.apiName);
    if (!apiName) return [];
    return [{
      apiName,
      displayName: stringValue(value.displayName) || apiName,
      dataType: stringValue(value.dataType) || stringValue(value.type) || "string",
    }];
  });
}

export function linkedCriteriaProperties(
  objects: FoundryLiteOntologyObjectView[],
  links: OntologyCatalogLink[],
  target: string,
  targetKind: "object" | "interface",
): ActionBuilderLinkedPropertyOption[] {
  const anchors = new Set(
    targetKind === "object"
      ? [target]
      : objects.filter((item) => item.objectType.implements?.includes(target)).map((item) => item.apiName),
  );
  const options = links.flatMap((link) => [
    ...linkedOptionsForDirection(objects, anchors, link, "outgoing"),
    ...linkedOptionsForDirection(objects, anchors, link, "incoming"),
  ]);
  return options.sort((left, right) => left.label.localeCompare(right.label));
}

function linkedOptionsForDirection(
  objects: FoundryLiteOntologyObjectView[],
  anchors: Set<string>,
  link: OntologyCatalogLink,
  direction: "outgoing" | "incoming",
): ActionBuilderLinkedPropertyOption[] {
  const anchor = direction === "outgoing" ? link.fromObjectType : link.toObjectType;
  if (!anchors.has(anchor)) return [];
  const linkedType = direction === "outgoing" ? link.toObjectType : link.fromObjectType;
  const linked = objects.find((item) => item.apiName === linkedType);
  return (linked?.properties ?? []).map((property) => ({
    key: `${direction}:${link.apiName}:${property.apiName}`,
    linkType: link.apiName,
    direction,
    objectType: linkedType,
    property: property.apiName,
    label: `${link.displayName} · ${linkedType}.${property.apiName}`,
  }));
}

export function actionBuilderValidationMessage(draft: ActionBuilderDraft): string | null {
  if (!draft.apiName.trim()) return "API name을 입력하세요.";
  if (!draft.target) return `대상 ${draft.targetKind === "interface" ? "Interface" : "객체"}를 선택하세요.`;
  if (!commaSeparatedValues(draft.viewRoles).length) return "최소 한 개의 조회 역할이 필요합니다.";
  if (!commaSeparatedValues(draft.editRoles).length) return "최소 한 개의 편집 역할이 필요합니다.";
  if (!commaSeparatedValues(draft.applyRoles).length) return "최소 한 개의 실행 역할이 필요합니다.";
  const names = draft.parameters.map((parameter) => parameter.apiName.trim());
  if (names.some((name) => !name)) return "모든 파라미터에 API name이 필요합니다.";
  if (new Set(names).size !== names.length) return "파라미터 API name은 중복될 수 없습니다.";
  if (draft.parameters.some(needsReferenceType)) return "객체·인터페이스 파라미터에는 참조 타입이 필요합니다.";
  if (draft.parameters.some(needsMediaSet)) return "미디어·첨부파일 파라미터에는 namespace.name 형식의 Media Set이 필요합니다.";
  const mediaError = validateMediaParameters(draft.parameters);
  if (mediaError) return mediaError;
  const structError = validateStructParameters(draft.parameters);
  if (structError) return structError;
  const sectionError = validateFormSections(draft.sections, names);
  if (sectionError) return sectionError;
  const parameterError = validateActionBuilderParameterPolicies(draft.parameters);
  if (parameterError) return parameterError;
  if (draft.submissionCriteria) {
    const criteriaError = validateActionBuilderCondition(draft.submissionCriteria, new Set(names), true);
    if (criteriaError) return `제출 조건: ${criteriaError}`;
  }
  if (draft.executionMode === "function") {
    if (draft.targetKind === "interface") return "Interface Action은 선언형 편집 규칙만 사용할 수 있습니다.";
    if (!draft.functionApiName.trim() || !draft.functionVersion.trim()) return "version-pinned function을 선택하세요.";
    if (draft.functionExecutionMode === "batched" && !draft.functionBatchInputName.trim()) return "배치 함수의 list-of-struct 입력 이름이 필요합니다.";
    const maximum = draft.functionExecutionMode === "batched" ? 10_000 : 20;
    if (!Number.isInteger(draft.functionMaxBatchSize) || draft.functionMaxBatchSize < 1 || draft.functionMaxBatchSize > maximum) return `함수 배치 한도는 1~${maximum.toLocaleString()}이어야 합니다.`;
  } else {
    const ruleError = validateActionBuilderRules(draft.rules, {
      parameters: names,
      targetObjectType: draft.target,
      targetKind: draft.targetKind,
    });
    if (ruleError) return ruleError;
  }
  const effectError = validateActionBuilderEffects(draft.effects, draft.executionMode);
  if (effectError) return effectError;
  if (draft.compensationActionApiName.trim() && !draft.isRevertEnabled) {
    return "보상 Action을 지정하려면 안전한 되돌리기를 허용해야 합니다.";
  }
  if (draft.compensationActionApiName.trim() === draft.apiName.trim()) {
    return "보상 Action은 현재 Action 자신일 수 없습니다.";
  }
  const minimumRisk = actionBuilderMinimumRisk(draft);
  if (riskRank(draft.riskLevel) < riskRank(minimumRisk)) return `현재 계약은 최소 ${minimumRisk} 위험 등급이 필요합니다.`;
  return null;
}

export function actionBuilderMinimumRisk(draft: ActionBuilderDraft): "low" | "medium" | "high" {
  if (draft.executionMode === "function" || draft.effects.length) return "high";
  return draft.rules.reduce<"low" | "medium" | "high">((minimum, rule) => {
    const next = actionBuilderRuleMinimumRisk(rule.kind);
    return riskRank(next) > riskRank(minimum) ? next : minimum;
  }, "low");
}

function riskRank(value: "low" | "medium" | "high"): number {
  return value === "high" ? 2 : value === "medium" ? 1 : 0;
}

function functionExecutionModeValue(value: unknown): "per_request" | "batched" {
  return value === "batched" ? "batched" : "per_request";
}

function functionBatchSizeValue(functionDefinition: Record<string, unknown>): number {
  const mode = functionExecutionModeValue(functionDefinition.executionMode);
  const value = functionDefinition.maxBatchSize;
  return typeof value === "number" && Number.isInteger(value) ? value : mode === "batched" ? 10_000 : 20;
}

function parameterDefinition(
  parameter: ActionBuilderParameter,
  parameterTypes: Readonly<Record<string, string>>,
): Record<string, unknown> {
  const definition: Record<string, unknown> = {
    apiName: parameter.apiName.trim(),
    type: parameter.dataType,
    required: parameter.isRequired,
    description: parameter.description.trim() || undefined,
    constraints: actionBuilderConstraintsDefinition(parameter.constraints, parameter.dataType),
    default: defaultDefinition(parameter.defaultValue, parameter.dataType),
    overrides: parameter.overrides.map(
      (override) => overrideDefinition(override, parameter.dataType, parameterTypes),
    ),
  };
  if (parameter.dataType === "object") definition.objectType = parameter.referenceType;
  if (parameter.dataType === "interface") definition.interfaceType = parameter.referenceType;
  if (parameter.dataType === "array" || parameter.dataType === "objectSet") {
    definition.itemType = parameter.itemType || "string";
  }
  addMediaConfiguration(definition, parameter, parameterMediaKind(parameter));
  if (parameter.dataType === "struct") definition.fields = parameter.fields.map(structFieldDefinition);
  return removeUndefined(definition);
}

function submissionCriteriaDefinition(
  draft: ActionBuilderDraft,
  parameterTypes: Readonly<Record<string, string>>,
): Record<string, unknown> | undefined {
  if (!draft.submissionCriteria) return undefined;
  return {
    ...conditionDefinition(draft.submissionCriteria, parameterTypes),
    ...(draft.submissionMessage.trim() ? { message: draft.submissionMessage.trim() } : {}),
  };
}

function formLayoutDefinition(
  draft: ActionBuilderDraft,
  parameterTypes: Readonly<Record<string, string>>,
): Record<string, unknown> {
  const nameByKey = new Map(draft.parameters.map((parameter) => [parameter.key, parameter.apiName.trim()]));
  return {
    sections: draft.sections.map((section) => removeUndefined({
      id: section.id.trim(),
      title: section.title.trim(),
      description: section.description.trim() || undefined,
      columns: section.columns,
      isCollapsible: section.isCollapsible,
      isInitiallyCollapsed: section.isCollapsible && section.isInitiallyCollapsed,
      parameterNames: section.parameterKeys.map((key) => nameByKey.get(key)).filter(isString),
      visibleWhen: section.visibleWhen
        ? conditionDefinition(section.visibleWhen, parameterTypes)
        : undefined,
    })),
  };
}

function draftParameter(value: unknown, index: number): ActionBuilderParameter {
  const parameter = recordValue(value);
  return {
    key: `parameter-${index}-${stringValue(parameter.apiName)}`,
    apiName: stringValue(parameter.apiName),
    dataType: stringValue(parameter.type) || "string",
    description: stringValue(parameter.description),
    isRequired: parameter.required === true,
    referenceType: stringValue(parameter.objectType) || stringValue(parameter.interfaceType),
    itemType: stringValue(parameter.itemType) || "string",
    mediaSet: stringValue(parameter.mediaSet),
    allowedMimeTypes: arrayValue(parameter.allowedMimeTypes).filter(isString).join(", "),
    maxBytes: numberText(parameter.maxBytes),
    render: parameter.render === "textInput" ? "textInput" : "filePicker",
    fields: arrayValue(parameter.fields).map((field, fieldIndex) => draftStructField(field, fieldIndex)),
    constraints: actionBuilderConstraintsFromDefinition(parameter.constraints),
    defaultValue: defaultFromDefinition(parameter.default),
    overrides: overridesFromDefinition(parameter.overrides),
  };
}

function draftSections(value: unknown, parameters: ActionBuilderParameter[]): ActionBuilderFormSection[] {
  const keyByName = new Map(parameters.map((parameter) => [parameter.apiName, parameter.key]));
  const sections = arrayValue(value).map((item, index) => {
    const section = recordValue(item);
    return {
      key: `form-section-${index}-${stringValue(section.id)}`,
      id: stringValue(section.id) || `section-${index + 1}`,
      title: stringValue(section.title) || "Parameters",
      description: stringValue(section.description),
      columns: section.columns === 2 ? 2 as const : 1 as const,
      isCollapsible: section.isCollapsible === true,
      isInitiallyCollapsed: section.isInitiallyCollapsed === true,
      parameterKeys: arrayValue(section.parameterNames).flatMap((name) => {
        const key = typeof name === "string" ? keyByName.get(name) : undefined;
        return key ? [key] : [];
      }),
      visibleWhen: conditionFromDefinition(recordValue(section.visibleWhen)),
    };
  });
  if (sections.length) return sections;
  return [{ ...newActionBuilderFormSection(0), parameterKeys: parameters.map((parameter) => parameter.key) }];
}

function draftStructField(value: unknown, index: number): ActionBuilderStructField {
  const field = recordValue(value);
  return {
    key: `struct-field-${index}-${stringValue(field.apiName)}`,
    apiName: stringValue(field.apiName),
    dataType: stringValue(field.type) || "string",
    description: stringValue(field.description),
    isRequired: field.required === true,
    mediaSet: stringValue(field.mediaSet),
    allowedMimeTypes: arrayValue(field.allowedMimeTypes).filter(isString).join(", "),
    maxBytes: numberText(field.maxBytes),
    render: field.render === "textInput" ? "textInput" : "filePicker",
    fields: arrayValue(field.fields).map((child, childIndex) => draftStructField(child, childIndex)),
  };
}

function structFieldDefinition(field: ActionBuilderStructField): Record<string, unknown> {
  const definition = removeUndefined({
    apiName: field.apiName.trim(),
    type: field.dataType,
    required: field.isRequired,
    description: field.description.trim() || undefined,
    fields: field.dataType === "struct" ? field.fields.map(structFieldDefinition) : undefined,
  });
  addMediaConfiguration(definition, field, field.dataType === "media" || field.dataType === "attachment" ? field.dataType : null);
  return definition;
}

function validateStructParameters(parameters: ActionBuilderParameter[]): string | null {
  for (const parameter of parameters) {
    if (parameter.dataType !== "struct") continue;
    const error = validateStructFields(parameter.fields, parameter.apiName || "struct");
    if (error) return error;
  }
  return null;
}

function validateStructFields(fields: ActionBuilderStructField[], path: string): string | null {
  if (!fields.length) return `${path} struct에는 최소 한 개의 필드가 필요합니다.`;
  const names = fields.map((field) => field.apiName.trim());
  if (names.some((name) => !name)) return `${path} struct의 모든 필드에 API name이 필요합니다.`;
  if (new Set(names).size !== names.length) return `${path} struct 필드 API name은 중복될 수 없습니다.`;
  for (const field of fields) {
    if ((field.dataType === "media" || field.dataType === "attachment") && !isMediaSetRef(field.mediaSet)) {
      return `${path}.${field.apiName}에는 namespace.name 형식의 Media Set이 필요합니다.`;
    }
    if (field.dataType !== "struct") continue;
    const error = validateStructFields(field.fields, `${path}.${field.apiName}`);
    if (error) return error;
  }
  return null;
}

function validateFormSections(sections: ActionBuilderFormSection[], parameters: string[]): string | null {
  if (!sections.length) return "최소 한 개의 폼 섹션이 필요합니다.";
  const ids = sections.map((section) => section.id.trim());
  if (ids.some((id) => !id)) return "모든 폼 섹션에 ID가 필요합니다.";
  if (new Set(ids).size !== ids.length) return "폼 섹션 ID는 중복될 수 없습니다.";
  for (const section of sections) {
    if (!section.title.trim()) return `${section.id} 섹션 제목이 필요합니다.`;
    if (section.isInitiallyCollapsed && !section.isCollapsible) return `${section.id} 섹션은 접기 허용이 필요합니다.`;
    if (!section.visibleWhen) continue;
    if (!isParameterLiteralCondition(section.visibleWhen)) {
      return `${section.id} 섹션 표시 조건은 파라미터와 고정 값만 사용할 수 있습니다.`;
    }
    const error = validateActionBuilderCondition(section.visibleWhen, new Set(parameters));
    if (error) return `${section.id} 섹션 표시 조건: ${error}`;
  }
  return null;
}

function isParameterLiteralCondition(condition: ActionBuilderCondition): boolean {
  if (condition.nodeType === "group") return condition.children.every(isParameterLiteralCondition);
  if (condition.nodeType === "not") return isParameterLiteralCondition(condition.child);
  const allowed = new Set(["parameter", "literal"]);
  return allowed.has(condition.left.kind)
    && (condition.operator === "exists" || allowed.has(condition.right.kind));
}

function needsReferenceType(parameter: ActionBuilderParameter): boolean {
  return ["object", "interface"].includes(parameter.dataType) && !parameter.referenceType.trim();
}

function needsMediaSet(parameter: ActionBuilderParameter): boolean {
  return parameterMediaKind(parameter) !== null && !isMediaSetRef(parameter.mediaSet);
}

function validateMediaParameters(parameters: ActionBuilderParameter[]): string | null {
  for (const parameter of parameters) {
    const error = validateMediaConfiguration(parameter.apiName || "파라미터", parameter, parameterMediaKind(parameter));
    if (error) return error;
    if (parameter.dataType === "struct") {
      const nested = validateMediaFields(parameter.fields, parameter.apiName || "struct");
      if (nested) return nested;
    }
  }
  return null;
}

function validateMediaFields(fields: ActionBuilderStructField[], path: string): string | null {
  for (const field of fields) {
    const kind = field.dataType === "media" || field.dataType === "attachment" ? field.dataType : null;
    const error = validateMediaConfiguration(`${path}.${field.apiName}`, field, kind);
    if (error) return error;
    if (field.dataType === "struct") {
      const nested = validateMediaFields(field.fields, `${path}.${field.apiName}`);
      if (nested) return nested;
    }
  }
  return null;
}

function validateMediaConfiguration(
  label: string,
  source: Pick<ActionBuilderParameter, "mediaSet" | "allowedMimeTypes" | "maxBytes" | "render">,
  kind: string | null,
): string | null {
  if (kind === null) return null;
  if (!isMediaSetRef(source.mediaSet)) return `${label}의 Media Set 형식이 올바르지 않습니다.`;
  if (!source.maxBytes.trim()) return null;
  const maximum = Number(source.maxBytes);
  if (!Number.isSafeInteger(maximum) || maximum <= 0) return `${label}의 최대 byte는 양의 정수여야 합니다.`;
  if (kind === "attachment" && maximum > 200 * 1024 * 1024) return `${label} 첨부파일은 200MB를 넘을 수 없습니다.`;
  return null;
}

function parameterMediaKind(parameter: ActionBuilderParameter): string | null {
  if (parameter.dataType === "media" || parameter.dataType === "attachment") return parameter.dataType;
  if (["array", "objectSet"].includes(parameter.dataType) && ["media", "attachment"].includes(parameter.itemType)) {
    return parameter.itemType;
  }
  return null;
}

function addMediaConfiguration(
  definition: Record<string, unknown>,
  source: Pick<ActionBuilderParameter, "mediaSet" | "allowedMimeTypes" | "maxBytes" | "render">,
  kind: string | null,
): void {
  if (kind === null) return;
  definition.mediaSet = source.mediaSet.trim();
  const mimeTypes = commaSeparatedValues(source.allowedMimeTypes);
  if (mimeTypes.length) definition.allowedMimeTypes = mimeTypes;
  const maximum = Number(source.maxBytes);
  if (source.maxBytes.trim() && Number.isSafeInteger(maximum) && maximum > 0) definition.maxBytes = maximum;
  definition.render = source.render;
}

function isMediaSetRef(value: string): boolean {
  const parts = value.trim().split(".");
  return parts.length === 2 && parts.every(Boolean);
}

function numberText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "";
}

function targetName(value: unknown): string {
  return typeof value === "string" ? value : stringValue(recordValue(value).apiName);
}

function removeUndefined(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined));
}

function commaSeparatedValues(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function recordValue(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>) : {};
}

function arrayValue(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function stringValue(value: unknown): string { return typeof value === "string" ? value : ""; }
function isString(value: unknown): value is string { return typeof value === "string"; }
function riskLevelValue(value: unknown): ActionBuilderDraft["riskLevel"] {
  return value === "low" || value === "medium" ? value : "high";
}

function agentPolicyValue(value: unknown): ActionBuilderDraft["agentExecutionPolicy"] {
  return value === "plan_only" || value === "autonomous" ? value : "approval_required";
}

function targetKindValue(value: unknown): ActionBuilderDraft["targetKind"] {
  return value === "interface" ? "interface" : "object";
}
