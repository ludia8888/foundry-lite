import {
  foundryLiteCompareDecimalText,
  foundryLiteIsDecimalText,
} from "@foundry-lite/sdk/action-values";

export const FOUNDRY_LITE_ACTION_CONDITION_OPERATORS = [
  "eq",
  "neq",
  "in",
  "notIn",
  "lt",
  "lte",
  "gt",
  "gte",
  "contains",
  "containsAny",
  "startsWith",
  "matches",
  "eachIs",
  "eachIsNot",
  "exists",
] as const;

export type FoundryLiteActionConditionOperator =
  (typeof FOUNDRY_LITE_ACTION_CONDITION_OPERATORS)[number];

export type FoundryLiteActionConditionCurrentUser = {
  id?: string | null;
  groups?: readonly string[];
  attributes?: Readonly<Record<string, unknown>>;
};

export type FoundryLiteActionConditionContext = {
  parameters: Readonly<Record<string, unknown>>;
  parameterTypes?: Readonly<Record<string, string>>;
  objectProperties?: Readonly<Record<string, unknown>>;
  objectPropertyTypes?: Readonly<Record<string, string>>;
  currentUser?: FoundryLiteActionConditionCurrentUser | null;
  linkedObjectProperties?: Readonly<Record<string, unknown>>;
  linkedObjectPropertyTypes?: Readonly<Record<string, string>>;
};

const CONDITION_NODE_FIELDS = ["all", "any", "not", "op"] as const;
const MAX_REGEX_PATTERN_CHARS = 256;
const MAX_REGEX_INPUT_CHARS = 4096;
const MISSING = Symbol("foundry-lite-action-condition-missing");
const TYPE_MISMATCH = Symbol("foundry-lite-action-condition-type-mismatch");
type ConditionResult = boolean | null;

/** Evaluate the canonical Action condition AST without coercion or fail-open negation. */
export function foundryLiteActionConditionMatches(
  raw: unknown,
  context: FoundryLiteActionConditionContext,
): boolean {
  return evaluateCondition(raw, context) === true;
}

export function foundryLiteActionConditionValuesEqual(left: unknown, right: unknown): boolean {
  if (left === MISSING || right === MISSING) return false;
  if (typeof left === "number" || typeof right === "number") {
    return typeof left === "number"
      && typeof right === "number"
      && Number.isFinite(left)
      && Number.isFinite(right)
      && left === right;
  }
  if (typeof left === "boolean" || typeof right === "boolean") return left === right;
  if (left === null || right === null) return left === right;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((item, index) => foundryLiteActionConditionValuesEqual(item, right[index]));
  }
  if (isRecord(left) || isRecord(right)) return recordValuesEqual(left, right);
  return typeof left === typeof right && left === right;
}

function evaluateCondition(raw: unknown, context: FoundryLiteActionConditionContext): ConditionResult {
  if (!isRecord(raw)) return null;
  const kinds = CONDITION_NODE_FIELDS.filter((field) => Object.hasOwn(raw, field));
  if (kinds.length !== 1) return null;
  const kind = kinds[0];
  if (kind === undefined) return null;
  if (!hasExactNodeFields(raw, kind)) return null;
  if (kind === "all" || kind === "any") return evaluateGroup(kind, raw[kind], context);
  if (kind === "not") {
    const result = evaluateCondition(raw.not, context);
    return result === null ? null : !result;
  }
  return evaluateComparison(raw, context);
}

function evaluateGroup(
  kind: "all" | "any",
  raw: unknown,
  context: FoundryLiteActionConditionContext,
): ConditionResult {
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const results = raw.map((child) => evaluateCondition(child, context));
  const decisive = kind === "all" ? false : true;
  if (results.includes(decisive)) return decisive;
  if (results.includes(null)) return null;
  return !decisive;
}

function evaluateComparison(
  condition: Record<string, unknown>,
  context: FoundryLiteActionConditionContext,
): ConditionResult {
  if (!isOperator(condition.op)) return null;
  const left = conditionValue(condition.left, context);
  if (condition.op === "exists") return hasValue(left);
  const right = conditionValue(condition.right, context);
  if (left === MISSING || right === MISSING) return null;
  const dataType = comparisonDataType(condition.left, condition.right, context);
  if (dataType === TYPE_MISMATCH) return false;
  return compareValues(condition.op, left, right, dataType);
}

function compareValues(
  operator: Exclude<FoundryLiteActionConditionOperator, "exists">,
  left: unknown,
  right: unknown,
  dataType: string | null,
): boolean {
  if (dataType === "decimal") return compareDecimalValues(operator, left, right);
  if (operator === "eq") return foundryLiteActionConditionValuesEqual(left, right);
  if (operator === "neq") return !foundryLiteActionConditionValuesEqual(left, right);
  if (operator === "in") return Array.isArray(right) && containsValue(right, left);
  if (operator === "notIn") return Array.isArray(right) && !containsValue(right, left);
  if (operator === "contains") return contains(left, right);
  if (operator === "containsAny") return containsAny(left, right);
  if (operator === "startsWith") return typeof left === "string" && typeof right === "string"
    && left.startsWith(right);
  if (operator === "matches") return regexMatches(left, right);
  if (operator === "eachIs") return Array.isArray(left)
    && left.every((item) => foundryLiteActionConditionValuesEqual(item, right));
  if (operator === "eachIsNot") return Array.isArray(left)
    && left.every((item) => !foundryLiteActionConditionValuesEqual(item, right));
  return orderedCompare(operator, left, right);
}

function compareDecimalValues(
  operator: Exclude<FoundryLiteActionConditionOperator, "exists">,
  left: unknown,
  right: unknown,
): boolean {
  if (operator === "in" || operator === "notIn") {
    const isContained = decimalCollectionContains(right, left);
    return operator === "in" ? isContained : Array.isArray(right) && !isContained;
  }
  if (operator === "contains") return decimalCollectionContains(left, right);
  if (operator === "eachIs" || operator === "eachIsNot") return eachDecimalIs(operator, left, right);
  if (!foundryLiteIsDecimalText(left) || !foundryLiteIsDecimalText(right)) return false;
  const order = foundryLiteCompareDecimalText(left, right);
  if (operator === "eq") return order === 0;
  if (operator === "neq") return order !== 0;
  if (operator === "lt") return order < 0;
  if (operator === "lte") return order <= 0;
  if (operator === "gt") return order > 0;
  return operator === "gte" && order >= 0;
}

function decimalCollectionContains(container: unknown, member: unknown): boolean {
  if (!Array.isArray(container) || !foundryLiteIsDecimalText(member)) return false;
  return container.some((item) => foundryLiteIsDecimalText(item)
    && foundryLiteCompareDecimalText(item, member) === 0);
}

function eachDecimalIs(operator: "eachIs" | "eachIsNot", left: unknown, right: unknown): boolean {
  if (!Array.isArray(left) || !foundryLiteIsDecimalText(right)) return false;
  const comparisons = left.map((item) => foundryLiteIsDecimalText(item)
    && foundryLiteCompareDecimalText(item, right) === 0);
  return operator === "eachIs" ? comparisons.every(Boolean) : comparisons.every((item) => !item);
}

function comparisonDataType(
  left: unknown,
  right: unknown,
  context: FoundryLiteActionConditionContext,
): string | null | typeof TYPE_MISMATCH {
  const leftType = conditionValueDataType(left, context);
  const rightType = conditionValueDataType(right, context);
  if (leftType && rightType && leftType !== rightType) return TYPE_MISMATCH;
  return leftType ?? rightType;
}

function conditionValueDataType(
  raw: unknown,
  context: FoundryLiteActionConditionContext,
): string | null {
  if (!isRecord(raw) || typeof raw.kind !== "string") return null;
  if (raw.kind === "parameter" && typeof raw.parameter === "string") {
    return ownText(context.parameterTypes, raw.parameter);
  }
  if (raw.kind === "objectProperty" && typeof raw.property === "string") {
    return ownText(context.objectPropertyTypes, raw.property);
  }
  if (raw.kind === "linkedObjectProperty") {
    const key = linkedObjectKey(raw);
    return key === null ? null : ownText(context.linkedObjectPropertyTypes, key);
  }
  return null;
}

function ownText(values: Readonly<Record<string, string>> | undefined, key: string): string | null {
  const value = values && Object.hasOwn(values, key) ? values[key] : undefined;
  return typeof value === "string" && value ? value : null;
}

function conditionValue(raw: unknown, context: FoundryLiteActionConditionContext): unknown {
  if (!isRecord(raw) || typeof raw.kind !== "string") return MISSING;
  if (raw.kind === "literal" && hasExactFields(raw, ["kind", "value"])) return raw.value;
  if (raw.kind === "parameter" && hasExactFields(raw, ["kind", "parameter"])
      && typeof raw.parameter === "string") {
    return ownValue(context.parameters, raw.parameter);
  }
  if (raw.kind === "objectProperty" && hasExactFields(raw, ["kind", "property"])
      && typeof raw.property === "string") {
    return ownValue(context.objectProperties, raw.property);
  }
  if (raw.kind === "currentUser" && hasAllowedFields(raw, ["kind", "attribute"])) {
    return currentUserValue(raw.attribute, context.currentUser);
  }
  if (raw.kind === "linkedObjectProperty"
      && hasRequiredAndAllowedFields(raw, ["kind", "linkType", "property"], ["direction", "aggregation"])) {
    return linkedObjectValue(raw, context.linkedObjectProperties);
  }
  return MISSING;
}

function currentUserValue(
  attribute: unknown,
  currentUser: FoundryLiteActionConditionCurrentUser | null | undefined,
): unknown {
  if (!currentUser) return MISSING;
  if (attribute === undefined || attribute === "id") return currentUser.id ?? MISSING;
  if (["group", "groups", "roles"].includes(String(attribute))) return currentUser.groups ?? MISSING;
  return typeof attribute === "string" ? ownValue(currentUser.attributes, attribute) : MISSING;
}

function linkedObjectValue(
  value: Record<string, unknown>,
  linkedValues: Readonly<Record<string, unknown>> | undefined,
): unknown {
  if (typeof value.linkType !== "string" || typeof value.property !== "string") return MISSING;
  const key = linkedObjectKey(value);
  return key === null ? MISSING : ownValue(linkedValues, key);
}

function linkedObjectKey(value: Record<string, unknown>): string | null {
  if (typeof value.linkType !== "string" || typeof value.property !== "string") return null;
  const direction = value.direction === "incoming" ? "incoming" : "outgoing";
  const aggregation = value.aggregation === "count" ? "count" : "values";
  return `${direction}:${value.linkType}:${value.property}:${aggregation}`;
}

function ownValue(values: Readonly<Record<string, unknown>> | undefined, key: string): unknown {
  return values && Object.hasOwn(values, key) ? values[key] : MISSING;
}

function hasValue(value: unknown): boolean {
  if (value === MISSING || value === null || value === undefined) return false;
  if (typeof value === "string" || Array.isArray(value)) return value.length > 0;
  if (isRecord(value)) return Object.keys(value).length > 0;
  return true;
}

function contains(container: unknown, member: unknown): boolean {
  if (typeof container === "string") return typeof member === "string" && container.includes(member);
  return Array.isArray(container) && containsValue(container, member);
}

function containsAny(container: unknown, members: unknown): boolean {
  return Array.isArray(container) && Array.isArray(members)
    && members.some((member) => containsValue(container, member));
}

function containsValue(container: readonly unknown[], member: unknown): boolean {
  return container.some((item) => foundryLiteActionConditionValuesEqual(item, member));
}

function orderedCompare(operator: string, left: unknown, right: unknown): boolean {
  if (typeof left === "number" && typeof right === "number") {
    if (!Number.isFinite(left) || !Number.isFinite(right)) return false;
    return compareOrdered(operator, left, right);
  }
  if (typeof left !== "string" || typeof right !== "string") return false;
  const leftTimestamp = parseTimestamp(left);
  const rightTimestamp = parseTimestamp(right);
  if (leftTimestamp !== null || rightTimestamp !== null || looksLikeTimestamp(left) || looksLikeTimestamp(right)) {
    return leftTimestamp !== null && rightTimestamp !== null
      && compareBigIntOrdered(operator, leftTimestamp, rightTimestamp);
  }
  return compareOrdered(operator, left, right);
}

function looksLikeTimestamp(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}[Tt]/u.test(value);
}

function parseTimestamp(value: string): bigint | null {
  const parts = /^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/u
    .exec(value);
  if (!parts) return null;
  const [year, month, day, hour, minute, second] = parts.slice(1, 7).map(Number);
  if (year === undefined || month === undefined || day === undefined || hour === undefined
      || minute === undefined || second === undefined || year < 1 || month < 1 || month > 12
      || day < 1 || day > daysInMonth(year, month)
      || hour > 23 || minute > 59 || second > 59) return null;
  const offsetHour = Number(parts[10] ?? 0);
  const offsetMinute = Number(parts[11] ?? 0);
  if (offsetHour > 23 || offsetMinute > 59) return null;
  const offsetSign = parts[9] === "-" ? -1 : 1;
  const offsetSeconds = offsetSign * ((offsetHour * 60 + offsetMinute) * 60);
  const localSeconds = daysFromCivil(year, month, day) * 86_400 + hour * 3_600 + minute * 60 + second;
  if (!Number.isSafeInteger(localSeconds)) return null;
  const fractionalMicros = Number((parts[7] ?? "").padEnd(6, "0"));
  return BigInt(localSeconds - offsetSeconds) * 1_000_000n + BigInt(fractionalMicros);
}

function daysFromCivil(year: number, month: number, day: number): number {
  const adjustedYear = year - (month <= 2 ? 1 : 0);
  const era = Math.floor(adjustedYear / 400);
  const yearOfEra = adjustedYear - era * 400;
  const adjustedMonth = month + (month > 2 ? -3 : 9);
  const dayOfYear = Math.floor((153 * adjustedMonth + 2) / 5) + day - 1;
  const dayOfEra = yearOfEra * 365 + Math.floor(yearOfEra / 4) - Math.floor(yearOfEra / 100) + dayOfYear;
  return era * 146_097 + dayOfEra - 719_468;
}

function daysInMonth(year: number, month: number): number {
  if (month === 2) return isLeapYear(year) ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

function isLeapYear(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function compareBigIntOrdered(operator: string, left: bigint, right: bigint): boolean {
  if (operator === "lt") return left < right;
  if (operator === "lte") return left <= right;
  if (operator === "gt") return left > right;
  if (operator === "gte") return left >= right;
  return false;
}

function compareOrdered(operator: string, left: number | string, right: number | string): boolean {
  if (operator === "lt") return left < right;
  if (operator === "lte") return left <= right;
  if (operator === "gt") return left > right;
  if (operator === "gte") return left >= right;
  return false;
}

function regexMatches(left: unknown, pattern: unknown): boolean {
  if (typeof left !== "string" || typeof pattern !== "string") return false;
  if (left.length > MAX_REGEX_INPUT_CHARS || !isSafeRegexPattern(pattern)) return false;
  try {
    return new RegExp(`^(?:${pattern})(?![\\s\\S])`, "u").test(left);
  } catch {
    return false;
  }
}

function isSafeRegexPattern(pattern: string): boolean {
  if (pattern.length > MAX_REGEX_PATTERN_CHARS || /\\[1-9]/u.test(pattern)) return false;
  const masked = maskRegexClassesAndEscapes(pattern);
  if (/\(\?(?!:)/u.test(masked)) return false;
  const stack: { hasRepeat: boolean; hasAlternation: boolean }[] = [];
  for (let index = 0; index < masked.length; index += 1) {
    const token = masked[index];
    if (token === "(") stack.push({ hasRepeat: false, hasAlternation: false });
    else if (token === "?" && index > 0 && masked[index - 1] === "(") continue;
    else if (token === "|" && stack.length) stack.at(-1)!.hasAlternation = true;
    else if ("*+?{".includes(token) && stack.length) stack.at(-1)!.hasRepeat = true;
    else if (token === ")" && !closeRegexGroupSafely(masked, index, stack)) return false;
  }
  try {
    new RegExp(pattern, "u");
    return true;
  } catch {
    return false;
  }
}

function closeRegexGroupSafely(
  pattern: string,
  closeIndex: number,
  stack: { hasRepeat: boolean; hasAlternation: boolean }[],
): boolean {
  const group = stack.pop();
  if (!group) return true;
  const isRepeated = regexQuantifierAt(pattern, closeIndex + 1);
  if (isRepeated && (group.hasRepeat || group.hasAlternation)) return false;
  const parent = stack.at(-1);
  if (parent) {
    parent.hasRepeat ||= group.hasRepeat;
    parent.hasAlternation ||= group.hasAlternation;
  }
  return true;
}

function regexQuantifierAt(pattern: string, index: number): boolean {
  const suffix = pattern.slice(index);
  return /^[*+?]/u.test(suffix) || /^\{\d+(?:,\d*)?\}/u.test(suffix);
}

function maskRegexClassesAndEscapes(pattern: string): string {
  const masked = [...pattern];
  let isEscaped = false;
  let isClass = false;
  for (let index = 0; index < masked.length; index += 1) {
    const token = masked[index];
    if (isEscaped) {
      masked[index - 1] = "_";
      masked[index] = "_";
      isEscaped = false;
    } else if (token === "\\") isEscaped = true;
    else if (isClass) {
      masked[index] = "_";
      isClass = token !== "]";
    } else if (token === "[") {
      masked[index] = "_";
      isClass = true;
    }
  }
  return masked.join("");
}

function recordValuesEqual(left: unknown, right: unknown): boolean {
  if (!isRecord(left) || !isRecord(right)) return false;
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key) => Object.hasOwn(right, key)
      && foundryLiteActionConditionValuesEqual(left[key], right[key]));
}

function isOperator(value: unknown): value is FoundryLiteActionConditionOperator {
  return typeof value === "string"
    && (FOUNDRY_LITE_ACTION_CONDITION_OPERATORS as readonly string[]).includes(value);
}

function hasExactNodeFields(
  value: Record<string, unknown>,
  kind: (typeof CONDITION_NODE_FIELDS)[number],
): boolean {
  const allowed = kind === "op" ? ["op", "left", "right", "message", "policyName"]
    : [kind, "message", "policyName"];
  if (!hasAllowedFields(value, allowed)) return false;
  if (kind !== "op") return true;
  if (!Object.hasOwn(value, "left")) return false;
  return value.op === "exists" ? !Object.hasOwn(value, "right") : Object.hasOwn(value, "right");
}

function hasExactFields(value: Record<string, unknown>, fields: readonly string[]): boolean {
  return hasRequiredAndAllowedFields(value, fields, []);
}

function hasRequiredAndAllowedFields(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[],
): boolean {
  return required.every((field) => Object.hasOwn(value, field))
    && hasAllowedFields(value, [...required, ...optional]);
}

function hasAllowedFields(value: Record<string, unknown>, fields: readonly string[]): boolean {
  const allowed = new Set(fields);
  return Object.keys(value).every((field) => allowed.has(field));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
