import {
  foundryLiteActionEnumHasDuplicates,
  foundryLiteActionEnumValues,
  foundryLiteActionScalarError,
  foundryLiteCompareDecimalText,
  foundryLiteIsDecimalText,
} from "@foundry-lite/sdk/action-values";

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
  const enumValues = foundryLiteActionEnumValues(dataType, value.enumValues);
  if (enumValues.length) definition.enum = enumValues;
  if (dataType === "string") {
    addNumber(definition, "minLength", value.minLength);
    addNumber(definition, "maxLength", value.maxLength);
  }
  if (["integer", "long", "float", "decimal"].includes(dataType)) {
    const addBound = dataType === "decimal" ? addDecimal : addNumber;
    addBound(definition, "minimum", value.minimum);
    addBound(definition, "maximum", value.maximum);
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
  if (value.minimum.trim() && !isNumericConstraint(value.minimum, dataType)) return "최솟값은 숫자여야 합니다.";
  if (value.maximum.trim() && !isNumericConstraint(value.maximum, dataType)) return "최댓값은 숫자여야 합니다.";
  const boundsError = validateBounds(definition, dataType);
  if (boundsError) return boundsError;
  const values = Array.isArray(definition.enum) ? definition.enum : [];
  const scalarTypes = ["string", "boolean", "integer", "long", "float", "decimal", "date", "timestamp"];
  if (scalarTypes.includes(dataType) && values.some((item) => foundryLiteActionScalarError(dataType, item))) {
    return `선택 가능 값(enum)에 ${dataType} 형식이 아닌 값이 있습니다.`;
  }
  if (foundryLiteActionEnumHasDuplicates(dataType, values)) {
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
  if (dataType === "decimal" && typeof minimum === "string" && typeof maximum === "string"
    && foundryLiteCompareDecimalText(minimum, maximum) > 0) {
    return `${label} 최솟값은 최댓값보다 클 수 없습니다.`;
  }
  if (typeof minimum === "number" && typeof maximum === "number" && minimum > maximum) {
    return `${label} 최솟값은 최댓값보다 클 수 없습니다.`;
  }
  return null;
}

function addNumber(target: Record<string, unknown>, key: string, value: string): void {
  if (value.trim()) target[key] = Number(value);
}

function addDecimal(target: Record<string, unknown>, key: string, value: string): void {
  if (value.trim()) target[key] = value;
}

function isNumericConstraint(value: string, dataType: string): boolean {
  return dataType === "decimal" ? foundryLiteIsDecimalText(value) : Number.isFinite(Number(value));
}

function printableLiteral(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function numberText(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return typeof value === "string" && foundryLiteIsDecimalText(value) ? value : "";
}

function recordValue(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}
