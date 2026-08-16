import assert from "node:assert/strict";

import {
  FOUNDRY_LITE_ACTION_CONDITION_OPERATORS,
  foundryLiteActionConditionMatches,
  foundryLiteActionConditionValuesEqual,
} from "../../packages/sdk-ts/src/action-conditions.ts";
import {
  foundryLiteActionInputText,
  foundryLiteActionInputValue,
  foundryLiteActionConditionLiteralValue,
  foundryLiteActionEnumContains,
  foundryLiteActionEnumHasDuplicates,
  foundryLiteActionEnumValues,
  foundryLiteActionLiteralValue,
  foundryLiteActionScalarError,
  foundryLiteCompareDecimalText,
  foundryLiteDecimalConstraintError,
  foundryLiteIsDecimalText,
} from "../../packages/sdk-ts/src/action-values.ts";

assert.equal(foundryLiteActionInputValue("decimal", "9007199254740993.000000000000000001"), "9007199254740993.000000000000000001");
assert.equal(foundryLiteActionInputValue("integer", "42"), 42);
assert.equal(foundryLiteIsDecimalText("1_000.00"), false);
assert.equal(foundryLiteIsDecimalText("+1.25e3"), true);
assert.equal(foundryLiteActionScalarError("integer", 2 ** 31), "integer");
assert.equal(foundryLiteActionScalarError("integer", 1.5), "integer");
assert.equal(foundryLiteActionScalarError("long", 2 ** 53), "integer");
assert.equal(foundryLiteActionScalarError("float", Number.POSITIVE_INFINITY), "float");
assert.equal(foundryLiteActionScalarError("date", "2026-02-29"), "date");
assert.equal(foundryLiteActionScalarError("date", "2028-02-29"), null);
assert.equal(foundryLiteActionScalarError("timestamp", "2026-08-14T09:30:00"), "timestamp");
assert.equal(foundryLiteActionScalarError("timestamp", "2026-08-14T09:30:00Z"), null);
assert.equal(foundryLiteCompareDecimalText("9007199254740993.000000000000000001", "9007199254740993"), 1);
assert.equal(foundryLiteCompareDecimalText("-10.01", "-10.001"), -1);
assert.equal(foundryLiteCompareDecimalText("1e999999999999999999", "9e999999999999999998"), 1);
assert.equal(foundryLiteDecimalConstraintError("amount", "0.09", { format: "decimal", "x-foundry-decimal-minimum": "0.10" }), "amount: minimum");
assert.equal(foundryLiteDecimalConstraintError("amount", "0.100", { format: "decimal", "x-foundry-decimal-minimum": "0.10" }), null);
assert.equal(foundryLiteActionLiteralValue("decimal", "9007199254740993.000000000000000001"), "9007199254740993.000000000000000001");
assert.equal(foundryLiteActionLiteralValue("string", "true"), "true");
assert.equal(foundryLiteActionLiteralValue("boolean", "true"), true);
assert.deepEqual(
  foundryLiteActionEnumValues("decimal", "9007199254740993.000000000000000001, 2.0"),
  ["9007199254740993.000000000000000001", "2.0"],
);
assert.deepEqual(
  foundryLiteActionConditionLiteralValue("decimal", "in", false, "[9007199254740993.000000000000000001, 2.0]"),
  ["9007199254740993.000000000000000001", "2.0"],
);
assert.equal(foundryLiteActionEnumContains("decimal", ["1"], "1.000"), true);
assert.equal(foundryLiteActionEnumHasDuplicates("decimal", ["1", "1.0"]), true);
assert.equal(foundryLiteActionEnumHasDuplicates("float", [1, 1.0]), true);
assert.equal(foundryLiteActionEnumContains("struct", [{ b: 2, a: 1 }], { a: 1, b: 2 }), true);
assert.match(String(foundryLiteActionInputValue("timestamp", "2026-08-14T09:30")), /(?:Z|[+-][0-9]{2}:[0-9]{2})$/);
assert.doesNotMatch(foundryLiteActionInputText("timestamp", "2026-08-14T09:30:00Z"), /Z$/);

const context = {
  parameters: { status: "OPEN", amount: "10", missingAsUndefined: undefined },
  parameterTypes: { status: "string", amount: "decimal" },
  objectProperties: { seats: 4, tags: ["quiet", "window"] },
  currentUser: {
    id: "u-1",
    groups: ["host"],
    attributes: { department: "sales" },
  },
  linkedObjectProperties: {
    "outgoing:reservations:status:values": ["HELD", "BOOKED"],
  },
};

const literal = (value) => ({ kind: "literal", value });
const comparison = (op, left, right) => ({ op, left: literal(left), right: literal(right) });

assert.deepEqual(
  FOUNDRY_LITE_ACTION_CONDITION_OPERATORS,
  [
    "eq", "neq", "in", "notIn", "lt", "lte", "gt", "gte", "contains", "containsAny",
    "startsWith", "matches", "eachIs", "eachIsNot", "exists",
  ],
);

assert.equal(foundryLiteActionConditionValuesEqual(true, 1), false);
assert.equal(foundryLiteActionConditionValuesEqual(-0, 0), true);
assert.equal(foundryLiteActionConditionValuesEqual({ items: [1, true] }, { items: [1.0, true] }), true);
assert.equal(foundryLiteActionConditionValuesEqual({ items: [1, true] }, { items: [1, 1] }), false);

for (const [condition, expected] of [
  [comparison("eq", true, 1), false],
  [comparison("neq", false, 0), true],
  [comparison("in", true, [1]), false],
  [comparison("notIn", "host", null), false],
  [comparison("lt", true, 2), false],
  [comparison("lte", 4, 4), true],
  [comparison("gt", "2026-08-14", "2026-08-13"), true],
  [comparison("lt", "2026-08-14T00:30:00+09:00", "2026-08-13T16:00:00Z"), true],
  [comparison("lt", "2026-08-14T00:00:00.000001Z", "2026-08-14T00:00:00.000002Z"), true],
  [comparison("gte", "2026-08-14T00:00:00.000001Z", "2026-08-14T00:00:00.000002Z"), false],
  [comparison("eq", "2026-08-14T01:00:00.000001+01:00", "2026-08-14T00:00:00.000001Z"), false],
  [comparison("lt", "2026-08-14T00:30:00", "2026-08-13T16:00:00Z"), false],
  [comparison("lt", "2026-08-14T00:30:00", "2026-08-14T01:30:00"), false],
  [comparison("lt", "2026-08-14T00:00:00.0000001Z", "2026-08-14T00:00:00.000002Z"), false],
  [comparison("lt", "0000-01-01T00:00:00Z", "2026-08-14T00:00:00Z"), false],
  [comparison("lt", "0001-01-01T00:00:00Z", "0099-01-01T00:00:00Z"), true],
  [comparison("lt", "2026-08-14T00:00:00+24:00", "2026-08-15T00:00:00Z"), false],
  [comparison("lt", "2026-02-30T00:00:00Z", "2026-03-01T00:00:00Z"), false],
  [comparison("gte", 4, 5), false],
  [comparison("contains", [1], true), false],
  [comparison("containsAny", [1, "quiet"], [true, "quiet"]), true],
  [comparison("startsWith", "BOOKED", "BOOK"), true],
  [comparison("matches", "FREE", "FR[EA]E"), true],
  [comparison("eachIs", ["FREE", "FREE"], "FREE"), true],
  [comparison("eachIsNot", [true], 1), true],
  [{ op: "exists", left: literal([]) }, false],
  [{ op: "exists", left: literal(false) }, true],
]) {
  assert.equal(foundryLiteActionConditionMatches(condition, context), expected, JSON.stringify(condition));
}

assert.equal(foundryLiteActionConditionMatches({
  op: "lte",
  left: { kind: "parameter", parameter: "status" },
  right: { kind: "objectProperty", property: "seats" },
}, context), false);
assert.equal(foundryLiteActionConditionMatches({
  op: "eq",
  left: { kind: "currentUser", attribute: "department" },
  right: literal("sales"),
}, context), true);
assert.equal(foundryLiteActionConditionMatches({
  op: "gt",
  left: { kind: "parameter", parameter: "amount" },
  right: literal("2"),
}, context), true);
assert.equal(foundryLiteActionConditionMatches({
  op: "eq",
  left: { kind: "parameter", parameter: "amount" },
  right: literal("10.00"),
}, context), true);
assert.equal(foundryLiteActionConditionMatches({
  op: "in",
  left: { kind: "parameter", parameter: "amount" },
  right: literal(["9", "10.0"]),
}, context), true);
assert.equal(foundryLiteActionConditionMatches({
  op: "contains",
  left: {
    kind: "linkedObjectProperty",
    linkType: "reservations",
    property: "status",
    direction: "outgoing",
    aggregation: "values",
  },
  right: literal("BOOKED"),
}, context), true);

const missingCondition = {
  op: "eq",
  left: { kind: "objectProperty", property: "missing" },
  right: literal("APPROVED"),
};
assert.equal(foundryLiteActionConditionMatches({ not: missingCondition }, context), false);
assert.equal(foundryLiteActionConditionMatches({ all: [comparison("eq", 1, 1), missingCondition] }, context), false);
assert.equal(foundryLiteActionConditionMatches({ any: [comparison("eq", 1, 1), missingCondition] }, context), true);
assert.equal(foundryLiteActionConditionMatches({ all: [] }, context), false);
assert.equal(foundryLiteActionConditionMatches({ all: [comparison("eq", 1, 1)], op: "eq" }, context), false);
assert.equal(foundryLiteActionConditionMatches({ ...comparison("eq", 1, 1), typo: true }, context), false);
assert.equal(foundryLiteActionConditionMatches({
  op: "eq",
  left: { kind: "parameter", parameter: "status", paramter: "status" },
  right: literal("OPEN"),
}, context), false);

for (const pattern of ["(a+)+$", "(a?)+$", "(a|aa)+$", String.raw`(a)\1`, "(?=a)a"] ) {
  assert.equal(foundryLiteActionConditionMatches(comparison("matches", "aaaa", pattern), context), false, pattern);
}
assert.equal(foundryLiteActionConditionMatches(comparison("matches", "abab", "(?:ab)+"), context), true);

console.log("Action condition OSDK contract passed");
