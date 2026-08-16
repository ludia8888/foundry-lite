const DECIMAL_PATTERN = /^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/;
const INTEGER_MIN = -(2 ** 31);
const INTEGER_MAX = 2 ** 31 - 1;
const LONG_MIN = -(2 ** 53 - 1);
const LONG_MAX = 2 ** 53 - 1;

/** Preserve the canonical wire representation for values edited in OSDK Action forms. */
export function foundryLiteActionInputValue(dataType: string, raw: string): unknown {
  if (raw === "") return undefined;
  if (dataType === "decimal") return raw;
  if (dataType === "timestamp") return timestampInputValue(raw);
  if (["integer", "long", "float"].includes(dataType)) return Number(raw);
  if (dataType === "boolean") return raw === "true";
  return raw;
}

export function foundryLiteActionInputText(dataType: string, value: unknown): string {
  if (typeof value !== "string") return "";
  if (dataType !== "timestamp" || !hasTimestampOffset(value)) return value;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return localTimestampText(parsed);
}

export function foundryLiteActionInputType(
  dataType: string,
): "date" | "datetime-local" | "number" | "text" {
  if (dataType === "date") return "date";
  if (dataType === "timestamp") return "datetime-local";
  if (["integer", "long", "float"].includes(dataType)) return "number";
  return "text";
}

/** Parse an authored Action literal without precision loss or accidental scalar coercion. */
export function foundryLiteActionLiteralValue(dataType: string | null | undefined, raw: string): unknown {
  if (["string", "decimal", "date", "timestamp"].includes(dataType ?? "")) return unquoteText(raw);
  return parseJsonLiteral(raw);
}

/** Parse a condition literal, preserving every Decimal digit inside membership lists. */
export function foundryLiteActionConditionLiteralValue(
  dataType: string | null | undefined,
  operator: string,
  isLeft: boolean,
  raw: string,
): unknown {
  const expectsDecimalCollection = dataType === "decimal"
    && ((!isLeft && ["in", "notIn"].includes(operator))
      || (isLeft && ["eachIs", "eachIsNot"].includes(operator)));
  return expectsDecimalCollection ? decimalLiteralList(raw) : foundryLiteActionLiteralValue(dataType, raw);
}

/** Parse comma-separated enum text according to the declared Action scalar type. */
export function foundryLiteActionEnumValues(dataType: string, raw: string): unknown[] {
  const items = raw.split(",").map((item) => item.trim()).filter(Boolean);
  if (["string", "decimal", "date", "timestamp"].includes(dataType)) return items.map(unquoteText);
  return items.map(parseJsonLiteral);
}

/** Match an enum using the declared scalar semantics and structural JSON equality. */
export function foundryLiteActionEnumContains(
  dataType: string,
  values: readonly unknown[],
  value: unknown,
): boolean {
  return values.some((candidate) => actionEnumValuesEqual(dataType, candidate, value));
}

/** Detect semantically duplicate enum members before an Action definition is submitted. */
export function foundryLiteActionEnumHasDuplicates(dataType: string, values: readonly unknown[]): boolean {
  return values.some((value, index) => foundryLiteActionEnumContains(dataType, values.slice(0, index), value));
}

export function foundryLiteDecimalConstraintError(
  name: string,
  value: unknown,
  constraints: Readonly<Record<string, unknown>>,
): string | null {
  if (!foundryLiteIsDecimalText(value)) return `${name}: decimal`;
  const minimum = constraints["x-foundry-decimal-minimum"];
  const maximum = constraints["x-foundry-decimal-maximum"];
  if (typeof minimum === "string" && foundryLiteCompareDecimalText(value, minimum) < 0) return `${name}: minimum`;
  if (typeof maximum === "string" && foundryLiteCompareDecimalText(value, maximum) > 0) return `${name}: maximum`;
  return null;
}

export function foundryLiteActionScalarError(dataType: string, value: unknown): string | null {
  if (dataType === "decimal") return foundryLiteIsDecimalText(value) ? null : "decimal";
  if (dataType === "integer") return integerError(value, INTEGER_MIN, INTEGER_MAX);
  if (dataType === "long") return integerError(value, LONG_MIN, LONG_MAX);
  if (dataType === "float") return typeof value === "number" && Number.isFinite(value) ? null : "float";
  if (dataType === "boolean") return typeof value === "boolean" ? null : "boolean";
  if (dataType === "date") return isIsoDate(value) ? null : "date";
  if (dataType === "timestamp") return isIsoTimestamp(value) ? null : "timestamp";
  if (dataType === "string") return typeof value === "string" ? null : "string";
  return null;
}

export function foundryLiteIsDecimalText(value: unknown): value is string {
  return typeof value === "string" && DECIMAL_PATTERN.test(value);
}

export function foundryLiteCompareDecimalText(left: string, right: string): number {
  const a = normalizedDecimal(left);
  const b = normalizedDecimal(right);
  if (a.sign !== b.sign) return a.sign < b.sign ? -1 : 1;
  if (a.digits === "0" && b.digits === "0") return 0;
  const magnitude = a.exponent === b.exponent ? compareDigits(a.digits, b.digits) : a.exponent < b.exponent ? -1 : 1;
  return a.sign < 0 ? -magnitude : magnitude;
}

function normalizedDecimal(value: string): { sign: -1 | 1; digits: string; exponent: bigint } {
  const match = /^([+-]?)([0-9]*)(?:\.([0-9]*))?(?:[eE]([+-]?[0-9]+))?$/.exec(value);
  if (!match) return { sign: 1, digits: "0", exponent: 0n };
  const sign = match[1] === "-" ? -1 : 1;
  const integer = match[2] ?? "";
  const fraction = match[3] ?? "";
  const rawDigits = `${integer}${fraction}`.replace(/^0+/, "") || "0";
  const explicitExponent = BigInt(match[4] ?? "0");
  const trailingZeros = rawDigits === "0" ? 0 : rawDigits.length - rawDigits.replace(/0+$/, "").length;
  const digits = rawDigits === "0" ? "0" : rawDigits.slice(0, rawDigits.length - trailingZeros);
  const exponent = rawDigits === "0"
    ? 0n
    : explicitExponent - BigInt(fraction.length) + BigInt(trailingZeros + digits.length);
  return { sign: rawDigits === "0" ? 1 : sign, digits, exponent };
}

function decimalLiteralList(raw: string): unknown {
  const value = raw.trim();
  if (!value.startsWith("[") || !value.endsWith("]")) return parseJsonLiteral(raw);
  const inner = value.slice(1, -1).trim();
  if (!inner) return [];
  return inner.split(",").map((item) => unquoteText(item.trim()));
}

function actionEnumValuesEqual(dataType: string, left: unknown, right: unknown): boolean {
  if (dataType === "decimal") {
    return foundryLiteIsDecimalText(left) && foundryLiteIsDecimalText(right)
      && foundryLiteCompareDecimalText(left, right) === 0;
  }
  if (["integer", "long", "float"].includes(dataType)) {
    return typeof left === "number" && typeof right === "number"
      && Number.isFinite(left) && Number.isFinite(right) && left === right;
  }
  const leftIdentity = boundedJsonIdentity(left, new WeakSet<object>(), 0);
  const rightIdentity = boundedJsonIdentity(right, new WeakSet<object>(), 0);
  return leftIdentity !== null && leftIdentity === rightIdentity;
}

function boundedJsonIdentity(value: unknown, seen: WeakSet<object>, depth: number): string | null {
  if (depth > 64) return null;
  if (value === null) return "null";
  if (typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") return Number.isFinite(value) ? JSON.stringify(value) : null;
  if (typeof value !== "object" || seen.has(value)) return null;
  seen.add(value);
  const identity = Array.isArray(value)
    ? jsonArrayIdentity(value, seen, depth)
    : jsonObjectIdentity(value, seen, depth);
  seen.delete(value);
  return identity;
}

function jsonArrayIdentity(value: readonly unknown[], seen: WeakSet<object>, depth: number): string | null {
  const members = value.map((item) => boundedJsonIdentity(item, seen, depth + 1));
  return members.every((item): item is string => item !== null) ? `[${members.join(",")}]` : null;
}

function jsonObjectIdentity(value: object, seen: WeakSet<object>, depth: number): string | null {
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return null;
  const entries = Object.entries(value).sort(([left], [right]) => left.localeCompare(right));
  const members = entries.map(([key, item]) => {
    const identity = boundedJsonIdentity(item, seen, depth + 1);
    return identity === null ? null : `${JSON.stringify(key)}:${identity}`;
  });
  return members.every((item): item is string => item !== null) ? `{${members.join(",")}}` : null;
}

function parseJsonLiteral(value: string): unknown {
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
}

function unquoteText(value: string): string {
  if (value.length < 2 || !value.startsWith('"') || !value.endsWith('"')) return value;
  const parsed = parseJsonLiteral(value);
  return typeof parsed === "string" ? parsed : value;
}

function compareDigits(left: string, right: string): number {
  const width = Math.max(left.length, right.length);
  const a = left.padEnd(width, "0");
  const b = right.padEnd(width, "0");
  return a === b ? 0 : a < b ? -1 : 1;
}

function timestampInputValue(raw: string): string {
  if (hasTimestampOffset(raw)) return raw;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? raw : parsed.toISOString();
}

function integerError(value: unknown, minimum: number, maximum: number): string | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum && value <= maximum
    ? null
    : "integer";
}

function isIsoDate(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= daysInMonth(year, month);
}

function isIsoTimestamp(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = Number(match[10] ?? 0);
  const offsetMinute = Number(match[11] ?? 0);
  return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= daysInMonth(year, month)
    && hour <= 23 && minute <= 59 && second <= 59 && offsetHour <= 23 && offsetMinute <= 59;
}

function daysInMonth(year: number, month: number): number {
  if (month === 2) return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

function hasTimestampOffset(value: string): boolean {
  return /(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(value);
}

function localTimestampText(value: Date): string {
  const year = String(value.getFullYear()).padStart(4, "0");
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  const hour = String(value.getHours()).padStart(2, "0");
  const minute = String(value.getMinutes()).padStart(2, "0");
  const second = String(value.getSeconds()).padStart(2, "0");
  const millisecond = String(value.getMilliseconds()).padStart(3, "0");
  return `${year}-${month}-${day}T${hour}:${minute}:${second}.${millisecond}`;
}
