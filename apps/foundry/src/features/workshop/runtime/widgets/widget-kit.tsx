import type { GenericObject } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteGenericObjectQuery,
  type FoundryLiteOntologyActionView,
  type FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { LayoutGrid, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import type { AppWidget, VariableFilter } from "../../lib/app-model";
import {
  applyRuntimeFilters,
  applyVariableFilters,
  useRuntimeState,
} from "../../lib/runtime-state";

/**
 * 모든 런타임 위젯 렌더러가 받는 공통 props.
 * 데이터 페치·필터는 위젯이 useWidgetObjects로 직접 수행하고,
 * 선택/필터/검색은 useRuntimeState/useRuntimeDispatch 컨텍스트로 공유한다.
 */
export type WidgetRuntimeProps = {
  widget: AppWidget;
  objectViewsByApiName: Record<string, FoundryLiteOntologyObjectView>;
  actionViews: readonly FoundryLiteOntologyActionView[];
};

export type WidgetObjectsResult = {
  allObjects: GenericObject[];
  objects: GenericObject[];
  isLoading: boolean;
  error: ReturnType<typeof useFoundryLiteGenericObjectQuery>["error"];
  reload: () => void;
};

/**
 * 위젯의 객체 타입을 조회하고 공유 런타임 필터·검색을 적용해 반환한다.
 * dataVersion이 바뀌면(액션 apply 후) 리페치한다.
 */
export function useWidgetObjects(
  objectApiName: string | null,
  variableFilters?: readonly VariableFilter[],
): WidgetObjectsResult {
  const client = useFoundryLiteClient();
  const state = useRuntimeState();
  const query = useFoundryLiteGenericObjectQuery(client, {
    objectApiName,
    pageSize: 200,
    key: ["workshop-widget", objectApiName ?? "none", state.dataVersion],
  });
  const allObjects = query.objects;
  const filtered = applyRuntimeFilters(allObjects, state);
  const objects = applyVariableFilters(
    filtered,
    variableFilters,
    state.variables,
  );
  return {
    allObjects,
    objects,
    isLoading: query.isLoading,
    error: query.error,
    reload: query.reload,
  };
}

export function objectViewFor(
  props: WidgetRuntimeProps,
  objectApiName: string | null,
): FoundryLiteOntologyObjectView | null {
  if (!objectApiName) return null;
  return props.objectViewsByApiName[objectApiName] ?? null;
}

export function actionViewFor(
  props: WidgetRuntimeProps,
  actionApiName: string | null | undefined,
): FoundryLiteOntologyActionView | null {
  if (!actionApiName) return null;
  return (
    props.actionViews.find((view) => view.apiName === actionApiName) ?? null
  );
}

/** 위젯 카드 프레임: 제목 헤더 + 본문. compact/borderless 옵션. */
export function WidgetFrame({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
  borderless,
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  borderless?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-md bg-white",
        !borderless && "border border-[#d5dce1]",
        className,
      )}
    >
      {title || actions ? (
        <div className="flex h-9 shrink-0 items-center gap-2 border-b border-[#e4e9ed] px-3">
          <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-[#1c2127]">
            {title}
          </span>
          {subtitle ? (
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
              {subtitle}
            </span>
          ) : null}
          {actions}
        </div>
      ) : null}
      <div className={cn("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </div>
  );
}

/** 데이터 미바인딩/빈 상태 안내 (Palantir식 중앙 아이콘 + 라벨 + 힌트). */
export function WidgetPlaceholder({
  label,
  hint,
  icon: Icon = LayoutGrid,
}: {
  label: string;
  hint?: string;
  icon?: LucideIcon;
}) {
  return (
    <div className="flex h-full min-h-[96px] flex-col items-center justify-center gap-2 p-4 text-center">
      <span className="flex size-9 items-center justify-center rounded-lg bg-[#eef1f4] text-[#a7b1bd]">
        <Icon className="size-4" />
      </span>
      <p className="text-[12px] font-medium text-[#5f6b7c]">{label}</p>
      {hint ? (
        <p className="max-w-[220px] text-[11px] text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
