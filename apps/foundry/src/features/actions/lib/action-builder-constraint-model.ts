export type ActionBuilderConstraints = {
  enumValues: string;
  minLength: string;
  maxLength: string;
  minimum: string;
  maximum: string;
  minItems: string;
  maxItems: string;
};

export function emptyActionBuilderConstraints(): ActionBuilderConstraints {
  return {
    enumValues: "",
    minLength: "",
    maxLength: "",
    minimum: "",
    maximum: "",
    minItems: "",
    maxItems: "",
  };
}

export function actionBuilderConstraintsFromDefinition(value: unknown): ActionBuilderConstraints {
  const source = recordValue(value);
  return {
    enumValues: Array.isArray(source.enum) ? source.enum.map(printableLiteral).join(", ") : "",
    minLength: numberText(source.minLength),
    maxLength: numberText(source.maxLength),
    minimum: numberText(source.minimum),
    maximum: numberText(source.maximum),
    minItems: numberText(source.minItems),
    maxItems: numberText(source.maxItems),
  };
}

export function actionBuilderConstraintsDefinition(
  value: ActionBuilderConstraints,
  dataType: string,
): Record<string, unknown> {
  const definition: Record<string, unknown> = {};
  const enumValues = commaSeparatedLiterals(value.enumValues);
  if (enumValues.length) definition.enum = enumValues;
  if (dataType === "string") {
    addNumber(definition, "minLength", value.minLength);
    addNumber(definition, "maxLength", value.maxLength);
  }
  if (["integer", "long", "float", "decimal"].includes(dataType)) {
    addNumber(definition, "minimum", value.minimum);
    addNumber(definition, "maximum", value.maximum);
  }
  if (["array", "objectSet"].includes(dataType)) {
    addNumber(definition, "minItems", value.minItems);
    addNumber(definition, "maxItems", value.maxItems);
  }
  return definition;
}

export function validateActionBuilderConstraints(
  value: ActionBuilderConstraints,
  dataType: string,
): string | null {
  const definition = actionBuilderConstraintsDefinition(value, dataType);
  const integerError = validateNonNegativeIntegers(value, dataType);
  if (integerError) return integerError;
  if (value.minimum.trim() && !Number.isFinite(Number(value.minimum))) return "최솟값은 숫자여야 합니다.";
  if (value.maximum.trim() && !Number.isFinite(Number(value.maximum))) return "최댓값은 숫자여야 합니다.";
  const boundsError = validateBounds(definition, dataType);
  if (boundsError) return boundsError;
  const values = Array.isArray(definition.enum) ? definition.enum : [];
  if (new Set(values.map((item) => JSON.stringify(item))).size !== values.length) {
    return "선택 가능 값(enum)은 중복될 수 없습니다.";
  }
  return null;
}

export function hasActionBuilderConstraints(value: ActionBuilderConstraints): boolean {
  return Object.values(value).some((item) => item.trim().length > 0);
}

function validateNonNegativeIntegers(value: ActionBuilderConstraints, dataType: string): string | null {
  const fields = dataType === "string"
    ? [["최소 길이", value.minLength], ["최대 길이", value.maxLength]]
    : ["array", "objectSet"].includes(dataType)
      ? [["최소 항목 수", value.minItems], ["최대 항목 수", value.maxItems]]
      : [];
  for (const [label, raw] of fields) {
    if (raw.trim() && (!Number.isSafeInteger(Number(raw)) || Number(raw) < 0)) {
      return `${label}는 0 이상의 정수여야 합니다.`;
    }
  }
  return null;
}

function validateBounds(definition: Record<string, unknown>, dataType: string): string | null {
  const [minimumKey, maximumKey, label] = dataType === "string"
    ? ["minLength", "maxLength", "문자 길이"]
    : ["array", "objectSet"].includes(dataType)
      ? ["minItems", "maxItems", "항목 수"]
      : ["minimum", "maximum", "숫자"];
  const minimum = definition[minimumKey];
  const maximum = definition[maximumKey];
  if (typeof minimum === "number" && typeof maximum === "number" && minimum > maximum) {
    return `${label} 최솟값은 최댓값보다 클 수 없습니다.`;
  }
  return null;
}

function addNumber(target: Record<string, unknown>, key: string, value: string): void {
  if (value.trim()) target[key] = Number(value);
}

function commaSeparatedLiterals(value: string): unknown[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean).map(parseLiteral);
}

function parseLiteral(value: string): unknown {
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
}

function printableLiteral(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function numberText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "";
}

function recordValue(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}
