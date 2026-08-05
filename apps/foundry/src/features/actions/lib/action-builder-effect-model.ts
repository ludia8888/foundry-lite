export const ACTION_BUILDER_EFFECT_KINDS = [
  "webhook", "notification", "event", "schedule_build", "connector_command",
] as const;

export type ActionBuilderEffectPayloadEntry = {
  key: string;
  name: string;
  value: string;
};

export type ActionBuilderEffectResponseField = {
  key: string;
  name: string;
  dataType: string;
};

export type ActionBuilderEffect = {
  key: string;
  effectId: string;
  kind: string;
  phase: "before_commit" | "after_commit";
  targetRef: string;
  maxAttempts: number;
  timeoutSeconds: number;
  payload: ActionBuilderEffectPayloadEntry[];
  responseFields: ActionBuilderEffectResponseField[];
};

export function newActionBuilderEffect(index: number): ActionBuilderEffect {
  return {
    key: `effect-new-${Date.now()}-${index}`,
    effectId: `effect-${index + 1}`,
    kind: "notification",
    phase: "after_commit",
    targetRef: "",
    maxAttempts: 3,
    timeoutSeconds: 30,
    payload: [],
    responseFields: [],
  };
}

export function newActionBuilderEffectPayloadEntry(index: number): ActionBuilderEffectPayloadEntry {
  return { key: `effect-payload-${Date.now()}-${index}`, name: "", value: "" };
}

export function newActionBuilderEffectResponseField(index: number): ActionBuilderEffectResponseField {
  return { key: `effect-response-${Date.now()}-${index}`, name: "", dataType: "string" };
}

export function actionBuilderEffectDefinition(effect: ActionBuilderEffect): Record<string, unknown> {
  return {
    effectId: effect.effectId.trim(),
    kind: effect.kind,
    phase: effect.phase,
    targetRef: effect.targetRef.trim(),
    maxAttempts: effect.maxAttempts,
    timeoutSeconds: effect.timeoutSeconds,
    payload: Object.fromEntries(effect.payload.map((entry) => [entry.name.trim(), parseLiteral(entry.value)])),
    responseFields: Object.fromEntries(effect.responseFields.map((entry) => [entry.name.trim(), entry.dataType])),
  };
}

export function actionBuilderEffectsFromDefinition(value: unknown): ActionBuilderEffect[] {
  return arrayValue(value).map((item, index) => effectFromDefinition(item, index));
}

export function validateActionBuilderEffects(
  effects: ActionBuilderEffect[],
  _executionMode: "rules" | "function",
): string | null {
  const ids = effects.map((effect) => effect.effectId.trim());
  if (ids.some((effectId) => !effectId)) return "모든 외부효과에 effect ID가 필요합니다.";
  if (new Set(ids).size !== ids.length) return "effect ID는 중복될 수 없습니다.";
  const before = effects.filter((effect) => effect.phase === "before_commit");
  if (before.length > 1) return "before-commit writeback은 Action당 하나만 허용됩니다.";
  if (before.some((effect) => effect.kind !== "webhook")) return "before-commit 외부효과는 webhook만 허용됩니다.";
  for (const effect of effects) {
    if (!effect.targetRef.trim()) return `${effect.effectId}: 등록된 target reference를 선택하거나 입력하세요.`;
    if (!Number.isInteger(effect.maxAttempts) || effect.maxAttempts < 1 || effect.maxAttempts > 10) return `${effect.effectId}: 최대 시도 횟수는 1~10이어야 합니다.`;
    if (!Number.isInteger(effect.timeoutSeconds) || effect.timeoutSeconds < 1 || effect.timeoutSeconds > 300) return `${effect.effectId}: timeout은 1~300초여야 합니다.`;
    const names = effect.payload.map((entry) => entry.name.trim());
    if (names.some((name) => !name) || new Set(names).size !== names.length) return `${effect.effectId}: payload field 이름이 비었거나 중복되었습니다.`;
    const responseNames = effect.responseFields.map((entry) => entry.name.trim());
    if (responseNames.some((name) => !name) || new Set(responseNames).size !== responseNames.length) return `${effect.effectId}: response field 이름이 비었거나 중복되었습니다.`;
    if (effect.phase !== "before_commit" && responseNames.length) return `${effect.effectId}: response field는 커밋 전 webhook에만 사용할 수 있습니다.`;
  }
  return null;
}

function effectFromDefinition(value: unknown, index: number): ActionBuilderEffect {
  const payload = recordValue(value);
  return {
    key: `effect-${index}-${stringValue(payload.effectId)}`,
    effectId: stringValue(payload.effectId) || `effect-${index + 1}`,
    kind: stringValue(payload.kind) || "notification",
    phase: payload.phase === "before_commit" ? "before_commit" : "after_commit",
    targetRef: stringValue(payload.targetRef),
    maxAttempts: integerValue(payload.maxAttempts, payload.phase === "before_commit" ? 1 : 3),
    timeoutSeconds: integerValue(payload.timeoutSeconds, 30),
    payload: Object.entries(recordValue(payload.payload)).map(([name, item], entryIndex) => ({
      key: `effect-payload-${index}-${entryIndex}-${name}`,
      name,
      value: printableLiteral(item),
    })),
    responseFields: Object.entries(recordValue(payload.responseFields)).map(([name, dataType], entryIndex) => ({
      key: `effect-response-${index}-${entryIndex}-${name}`,
      name,
      dataType: stringValue(dataType) || "string",
    })),
  };
}

function parseLiteral(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return "";
  try { return JSON.parse(trimmed) as unknown; } catch { return value; }
}

function printableLiteral(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value) ?? "";
}

function integerValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isInteger(value) ? value : fallback;
}

function recordValue(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>) : {};
}

function arrayValue(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function stringValue(value: unknown): string { return typeof value === "string" ? value : ""; }
