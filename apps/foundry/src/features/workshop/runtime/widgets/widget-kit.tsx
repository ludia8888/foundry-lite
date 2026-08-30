import type { GenericObject, ObjectQueryRequest, ObjectQueryResult } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteQuery,
  type FoundryLiteOntologyActionView,
  type FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { AlertTriangle, Inbox, LayoutGrid, LoaderCircle, LockKeyhole, type LucideIcon } from "lucide-react";
import { useCallback, useMemo, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import type { AppWidget, VariableFilter } from "../../lib/app-model";
import {
  applyRuntimeFilters,
  applyVariableFilters,
  useRuntimeState,
} from "../../lib/runtime-state";
import { useWorkshopRuntimeApplicationId, useWorkshopRuntimeDefinition } from "../runtime-application-context";

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
  error: ReturnType<typeof useFoundryLiteQuery<GenericObject[]>>["error"];
  reload: () => void;
  /** 상한에 걸려 일부만 읽었는가. 집계 위젯은 이걸 화면에 드러내야 한다. */
  isTruncated: boolean;
};

const WIDGET_PAGE_SIZE = 500;
/** 브라우저로 끌어올 수 있는 상한. 넘으면 자르되, 자른 사실을 숨기지 않는다. */
export const WIDGET_OBJECT_CAP = 10_000;

/**
 * 위젯의 객체 타입을 끝까지 읽고 공유 런타임 필터·검색을 적용해 반환한다.
 *
 * 예전에는 첫 200건 한 페이지만 읽고 그 위에서 집계했다. 차트는 그 표본을 전체인 양
 * 표시했기 때문에 1,000건짜리 객체 타입에서 "총 200건"이라고 단언했다. 표본이라는 표시가
 * 없는 수치는 틀린 수치보다 나쁘다 — 읽는 사람이 검증할 방법이 없기 때문이다.
 * dataVersion이 바뀌면(액션 apply 후) 리페치한다.
 */
export function useWidgetObjects(
  objectApiName: string | null,
  variableFilters?: readonly VariableFilter[],
): WidgetObjectsResult {
  const client = useFoundryLiteClient();
  const applicationId = useWorkshopRuntimeApplicationId();
  const state = useRuntimeState();
  const load = useCallback(async () => {
    if (!objectApiName) return [] as GenericObject[];
    const collected: GenericObject[] = [];
    let cursor: string | null = null;
    do {
      const options: ObjectQueryRequest = { limit: WIDGET_PAGE_SIZE, ...(cursor ? { cursor } : {}) };
      const page: ObjectQueryResult<GenericObject> = applicationId
        ? await client.aip.pilot.queryObjects(applicationId, objectApiName, options)
        : await client.objects.generic.query(objectApiName, options);
      collected.push(...page.items);
      cursor = page.nextCursor;
    } while (cursor && collected.length < WIDGET_OBJECT_CAP);
    return collected;
  }, [applicationId, client, objectApiName]);
  const query = useFoundryLiteQuery<GenericObject[]>(
    ["workshop-widget", objectApiName ?? "none", state.dataVersion],
    load,
    { enabled: Boolean(objectApiName) },
  );
  const allObjects = useMemo(() => query.data ?? [], [query.data]);
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
    isTruncated: allObjects.length >= WIDGET_OBJECT_CAP,
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
        "flex min-h-0 flex-col overflow-hidden rounded-2xl bg-white",
        !borderless &&
          "border border-[var(--workshop-line,#d5dce1)] shadow-[0_16px_42px_-36px_rgba(15,23,42,.65)]",
        className,
      )}
    >
      {title || actions ? (
        <div className="flex min-h-12 shrink-0 items-center gap-2 border-b border-[var(--workshop-line,#e4e9ed)] px-4 py-3">
          <span className="min-w-0 flex-1 truncate text-[13px] font-bold tracking-[-.01em] text-[var(--workshop-ink,#1c2127)]">
            {title}
          </span>
          {subtitle ? (
            <span className="shrink-0 text-[10px] font-medium text-muted-foreground">
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
    <div className="flex h-full min-h-[140px] flex-col items-center justify-center gap-2.5 p-6 text-center">
      <span className="flex size-11 items-center justify-center rounded-2xl bg-[var(--workshop-subtle,#eef1f4)] text-[#8b97a8]">
        <Icon className="size-5" />
      </span>
      <p className="text-[13px] font-bold text-[#475569]">{label}</p>
      {hint ? (
        <p className="max-w-[300px] text-[12px] leading-5 text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function WidgetDataState({
  isLoading,
  error,
  isEmpty,
  onRetry,
}: {
  isLoading: boolean;
  error: unknown;
  isEmpty: boolean;
  onRetry?: () => void;
}) {
  const definition = useWorkshopRuntimeDefinition();
  const copy = definition.presentation.feedback;
  if (isLoading) return <WidgetPlaceholder icon={LoaderCircle} label={copy.loadingTitle} hint={copy.loadingDescription} />;
  if (error) {
    const isForbidden = typeof error === "object" && error !== null && "status" in error && (error as { status?: number }).status === 403;
    return (
      <div className="relative">
        <WidgetPlaceholder icon={isForbidden ? LockKeyhole : AlertTriangle} label={isForbidden ? copy.forbiddenTitle : copy.errorTitle} hint={isForbidden ? copy.forbiddenDescription : copy.errorDescription} />
        {onRetry ? <button type="button" onClick={onRetry} className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-xl bg-[var(--workshop-accent)] px-4 py-2 text-[12px] font-bold text-white">다시 시도</button> : null}
      </div>
    );
  }
  if (isEmpty) return <WidgetPlaceholder icon={Inbox} label={copy.emptyTitle} hint={copy.emptyDescription} />;
  return null;
}
