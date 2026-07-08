import type {
  FoundryLiteOntologyActionView,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";

import type { WidgetSuggestion } from "./widget-catalog";

const NUMERIC_TYPES = new Set([
  "integer",
  "float",
  "double",
  "long",
  "decimal",
  "number",
  "bigint",
]);

const DATE_TYPES = new Set(["timestamp", "date", "datetime", "time"]);

export function isNumericType(dataType: string): boolean {
  return NUMERIC_TYPES.has(dataType.toLowerCase());
}

export function isDateType(dataType: string): boolean {
  return DATE_TYPES.has(dataType.toLowerCase());
}

/** 상태성 속성: 이름이 status/state 또는 첫 비-PK 문자열 속성. */
export function statusPropertyOf(
  view: FoundryLiteOntologyObjectView | null,
): string | null {
  if (!view) return null;
  const named = view.properties.find(
    (property) =>
      property.apiName.toLowerCase().includes("status") ||
      property.apiName.toLowerCase().includes("state"),
  );
  if (named) return named.apiName;
  const firstString = view.properties.find(
    (property) => property.dataType === "string" && !property.isPrimaryKey,
  );
  return firstString?.apiName ?? null;
}

export function numericPropertyOf(
  view: FoundryLiteOntologyObjectView | null,
): string | null {
  if (!view) return null;
  const numeric = view.properties.find((property) =>
    isNumericType(property.dataType),
  );
  return numeric?.apiName ?? null;
}

export function datePropertyOf(
  view: FoundryLiteOntologyObjectView | null,
): string | null {
  if (!view) return null;
  const date = view.properties.find((property) =>
    isDateType(property.dataType),
  );
  return date?.apiName ?? null;
}

/** 특정 객체 타입을 타깃으로 하는 액션 apiName 목록. */
export function actionsForObject(
  actionViews: readonly FoundryLiteOntologyActionView[],
  objectApiName: string | null,
): string[] {
  if (!objectApiName) return [];
  return actionViews
    .filter((view) => view.targetObjectApiName === objectApiName)
    .map((view) => view.apiName);
}

/**
 * 위젯 자동 바인딩 후보를 계산한다.
 * 기본 객체는 (1) 지정값 → (2) 액션이 있는 첫 객체 → (3) 첫 객체 순.
 */
export function buildWidgetSuggestion(
  objectViews: readonly FoundryLiteOntologyObjectView[],
  actionViews: readonly FoundryLiteOntologyActionView[],
  preferredObjectApiName?: string | null,
): WidgetSuggestion {
  const byActionable =
    objectViews.find((view) => view.actionCount > 0) ?? objectViews[0] ?? null;
  const chosen =
    (preferredObjectApiName
      ? objectViews.find((view) => view.apiName === preferredObjectApiName)
      : null) ??
    byActionable ??
    null;

  const objectApiName = chosen?.apiName ?? null;
  const actionApiNames = actionsForObject(actionViews, objectApiName);

  return {
    objectApiName,
    actionApiName: actionApiNames[0] ?? null,
    actionApiNames,
    statusProperty: statusPropertyOf(chosen),
    numericProperty: numericPropertyOf(chosen),
    dateProperty: datePropertyOf(chosen),
  };
}
