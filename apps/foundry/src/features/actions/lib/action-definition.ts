import type { FoundryLiteOntologyActionView } from "@foundry-lite/sdk/react";

/**
 * 온톨로지 카탈로그 액션의 raw `definition`(preconditions/mutations/writebacks/
 * permissions/sideEffects)을 화면 모델로 정규화한다. 카탈로그에 실재하는 메타만
 * 읽고, 없는 필드는 만들지 않는다 (fabricate 금지).
 */

export type ActionPrecondition = {
  message: string;
  expression: string | null;
};

export type ActionMutation = {
  type: string;
  property: string | null;
  value: string | null;
};

export type ActionWriteback = {
  apiName: string;
  mode: string | null;
  connector: string | null;
};

export type ActionSideEffect = {
  type: string;
  topic: string | null;
};

export type ActionDefinitionView = {
  viewRoles: string[];
  editRoles: string[];
  applyRoles: string[];
  preconditions: ActionPrecondition[];
  mutations: ActionMutation[];
  writebacks: ActionWriteback[];
  sideEffects: ActionSideEffect[];
  hasWritebacks: boolean;
  requiresApproval: boolean;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readDefinition(
  actionView: FoundryLiteOntologyActionView | null | undefined,
): Record<string, unknown> {
  const action = actionView?.action as Record<string, unknown> | undefined;
  return asRecord(action?.definition) ?? {};
}

export function actionDefinitionView(
  actionView: FoundryLiteOntologyActionView | null | undefined,
): ActionDefinitionView {
  const definition = readDefinition(actionView);
  const permissions = asRecord(definition.permissions);
  const roleValues = (value: unknown) => asArray(value)
    .map((role) => readString(role))
    .filter((role): role is string => role !== null);
  const viewRoles = roleValues(permissions?.viewRoles);
  const editRoles = roleValues(permissions?.editRoles);
  const applyRoles = roleValues(permissions?.applyRoles ?? permissions?.allowedRoles);

  const preconditions = asArray(definition.preconditions)
    .map((raw) => asRecord(raw))
    .filter((raw): raw is Record<string, unknown> => raw !== null)
    .map((raw) => ({
      message: readString(raw.message) ?? "제출 기준을 충족해야 합니다.",
      expression: readString(raw.safeExpression) ?? readString(raw.expression),
    }));

  const mutations = asArray(definition.mutations)
    .map((raw) => asRecord(raw))
    .filter((raw): raw is Record<string, unknown> => raw !== null)
    .map((raw) => ({
      type: readString(raw.type) ?? "mutation",
      property: readString(raw.property),
      value: readString(raw.value) ?? readString(raw.valueFrom),
    }));

  const writebacks = asArray(definition.writebacks)
    .map((raw) => asRecord(raw))
    .filter((raw): raw is Record<string, unknown> => raw !== null)
    .map((raw) => ({
      apiName: readString(raw.apiName) ?? "writeback",
      mode: readString(raw.mode),
      connector: readString(raw.connector),
    }));

  const sideEffects = asArray(definition.sideEffects)
    .map((raw) => asRecord(raw))
    .filter((raw): raw is Record<string, unknown> => raw !== null)
    .map((raw) => ({
      type: readString(raw.type) ?? "sideEffect",
      topic: readString(raw.topic),
    }));

  return {
    viewRoles,
    editRoles,
    applyRoles,
    preconditions,
    mutations,
    writebacks,
    sideEffects,
    hasWritebacks: writebacks.length > 0,
    requiresApproval: writebacks.length > 0,
  };
}
