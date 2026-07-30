import type { GenericObject } from "@foundry-lite/sdk";
import type { LucideIcon } from "lucide-react";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Check,
  ChevronsUpDown,
  Copy,
  Download,
  Link2,
  Minus,
  Pencil,
  Play,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import {
  formatCellValue,
  objectTitleOf,
  statusIntentOf,
  tableColumnNames,
} from "../../lib/app-model";
import { actionsForObject } from "../../lib/ontology-context";
import { useRuntimeDispatch, useRuntimeState } from "../../lib/runtime-state";
import { RuntimeActionForm } from "../RuntimeActionForm";
import {
  actionViewFor,
  objectViewFor,
  useWidgetObjects,
  WidgetFrame,
  WidgetPlaceholder,
  type WidgetRuntimeProps,
} from "./widget-kit";

const MISSING_OBJECT = (
  <WidgetPlaceholder
    label="객체 타입 미지정"
    hint="인스펙터에서 객체 타입을 선택하세요."
  />
);

/** 객체 타입별 아이콘 색 (Palantir 객체 타입 컬러 관례). */
const OBJECT_TYPE_PALETTE = [
  "#2d72d2",
  "#c8442a",
  "#238551",
  "#7961db",
  "#c87619",
  "#00847a",
  "#935610",
];
function objectTypeColor(objectType: string): string {
  let hash = 0;
  for (const ch of objectType) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return OBJECT_TYPE_PALETTE[hash % OBJECT_TYPE_PALETTE.length];
}

/** 컬럼 표시명: 객체 뷰의 displayName(Title Case) → apiName. */
function columnDisplayName(
  objectView: ReturnType<typeof objectViewFor>,
  apiName: string,
): string {
  const property = objectView?.properties.find((p) => p.apiName === apiName);
  return property?.displayName ?? apiName;
}

/** 범주형(태그로 렌더) 컬럼 판정. */
function isCategoricalColumn(name: string): boolean {
  return /status|state|urgency|priority|severity/i.test(name);
}

type SortState = { column: string; direction: "asc" | "desc" } | null;

function compareValues(a: unknown, b: unknown): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a ?? "").localeCompare(String(b ?? ""), "ko");
}

/**
 * 객체 테이블 (Palantir Object Table 클론):
 * 선택 체크박스 열(select-all indeterminate) + 객체 타입 컬러 아이콘 +
 * 정렬 가능한 Title Case 헤더 + 범주형 컬러 태그 + 선택 행 파란 강조.
 * 체크박스는 다중선택(로컬), 행 클릭은 공유 선택(상세·액션 구동).
 */
export function ObjectTableWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { objects, isLoading } = useWidgetObjects(
    objectApiName,
    widget.config.variableFilters,
  );
  const objectView = objectViewFor(props, objectApiName);
  const state = useRuntimeState();
  const dispatch = useRuntimeDispatch();
  const [sort, setSort] = useState<SortState>(null);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(() => new Set());
  const [menu, setMenu] = useState<CellMenu>(null);
  const [actionTarget, setActionTarget] = useState<ActionTarget>(null);

  const columns = useMemo(
    () =>
      tableColumnNames(
        objectView,
        objects,
        widget.config.propertyApiNames,
      ).slice(0, 8),
    [objectView, objects, widget.config.propertyApiNames],
  );

  const sortedObjects = useMemo(() => {
    if (!sort) return objects;
    const copy = [...objects];
    copy.sort((a, b) => {
      const result = compareValues(
        a.properties[sort.column],
        b.properties[sort.column],
      );
      return sort.direction === "asc" ? result : -result;
    });
    return copy;
  }, [objects, sort]);

  if (!objectApiName) return MISSING_OBJECT;

  const toggleSort = (column: string) =>
    setSort((current) => {
      if (!current || current.column !== column)
        return { column, direction: "asc" };
      if (current.direction === "asc") return { column, direction: "desc" };
      return null;
    });

  const allChecked =
    sortedObjects.length > 0 && checkedIds.size === sortedObjects.length;
  const someChecked = checkedIds.size > 0 && !allChecked;
  const toggleAll = () =>
    setCheckedIds(
      allChecked
        ? new Set()
        : new Set(sortedObjects.map((object) => object.objectId)),
    );
  const toggleOne = (objectId: string) =>
    setCheckedIds((current) => {
      const next = new Set(current);
      if (next.has(objectId)) next.delete(objectId);
      else next.add(objectId);
      return next;
    });

  const tint = objectApiName ? objectTypeColor(objectApiName) : "#2d72d2";
  const rowActionApiNames = actionsForObject(props.actionViews, objectApiName);

  return (
    <>
      <WidgetFrame
        title={widget.config.title || objectView?.displayName || objectApiName}
        subtitle={
          checkedIds.size > 0
            ? `${checkedIds.size}개 선택 · ${sortedObjects.length}`
            : `objects.generic.query · ${sortedObjects.length}`
        }
        className="min-h-[220px]"
        bodyClassName="overflow-auto"
      >
        <table className="w-full border-collapse text-[12px]">
          <thead className="sticky top-0 z-10 bg-[#f6f8fa]">
            <tr className="border-b border-[#d5dce1]">
              <th className="w-9 px-2 py-2 align-top">
                <TableCheckbox
                  variant={
                    allChecked
                      ? "checked"
                      : someChecked
                        ? "indeterminate"
                        : "unchecked"
                  }
                  onClick={toggleAll}
                />
              </th>
              {columns.map((name) => {
                const isSorted = sort?.column === name;
                return (
                  <th
                    key={name}
                    className="group max-w-[200px] px-3 py-2 text-left align-top"
                  >
                    <button
                      type="button"
                      onClick={() => toggleSort(name)}
                      className="flex items-start gap-1 text-left text-[12px] font-semibold text-[#5f6b7c] hover:text-[#1c2127]"
                    >
                      <span className="leading-tight">
                        {columnDisplayName(objectView, name)}
                      </span>
                      {isSorted ? (
                        sort?.direction === "asc" ? (
                          <ArrowUp className="mt-0.5 size-3 shrink-0 text-[#2d72d2]" />
                        ) : (
                          <ArrowDown className="mt-0.5 size-3 shrink-0 text-[#2d72d2]" />
                        )
                      ) : (
                        <ChevronsUpDown className="mt-0.5 size-3 shrink-0 text-[#c5ccd3] opacity-0 group-hover:opacity-100" />
                      )}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sortedObjects.map((object) => {
              const isSelected = object.objectId === state.selectedObjectId;
              const isChecked = checkedIds.has(object.objectId);
              return (
                <tr
                  key={object.objectId}
                  onClick={() =>
                    dispatch({
                      type: "selectObject",
                      objectId: object.objectId,
                    })
                  }
                  className={cn(
                    "cursor-pointer border-b border-[#eef1f4] last:border-b-0 hover:bg-[#f6f8fa]",
                    isSelected &&
                      "bg-[#e8f0fb] shadow-[inset_2px_0_0_#2d72d2] hover:bg-[#e8f0fb]",
                  )}
                >
                  <td
                    className="w-9 px-2 py-2 align-middle"
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleOne(object.objectId);
                    }}
                  >
                    <div className="flex items-center gap-1.5">
                      <TableCheckbox
                        variant={isChecked ? "checked" : "unchecked"}
                        onClick={() => toggleOne(object.objectId)}
                      />
                      <span
                        className="flex size-4 shrink-0 items-center justify-center rounded-[3px] text-[9px] font-bold text-white"
                        style={{ background: tint }}
                      >
                        {object.objectType.slice(0, 1).toUpperCase()}
                      </span>
                    </div>
                  </td>
                  {columns.map((name) => {
                    const value = object.properties[name];
                    return (
                      <td
                        key={name}
                        onContextMenu={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          dispatch({
                            type: "selectObject",
                            objectId: object.objectId,
                          });
                          setMenu({
                            x: event.clientX,
                            y: event.clientY,
                            object,
                            column: name,
                            value,
                          });
                        }}
                        className={cn(
                          "max-w-[220px] px-3 py-2 align-middle",
                          typeof value === "number" && "font-mono tabular-nums",
                        )}
                      >
                        {isCategoricalColumn(name) &&
                        typeof value === "string" ? (
                          <StatusPill intent={statusIntentOf(value)}>
                            {value}
                          </StatusPill>
                        ) : (
                          <span className="block truncate text-[#1c2127]">
                            {formatCellValue(value)}
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
            {sortedObjects.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length + 1}
                  className="h-16 px-3 text-center text-[11px] text-muted-foreground"
                >
                  {isLoading ? "불러오는 중…" : "조건에 맞는 객체가 없습니다."}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </WidgetFrame>
      {menu ? (
        <TableContextMenu
          menu={menu}
          objectView={objectView}
          rowActionApiNames={rowActionApiNames}
          actionViews={props.actionViews}
          onCopy={(value) => {
            void navigator.clipboard?.writeText(String(value ?? ""));
            setMenu(null);
          }}
          onRowAction={(actionApiName) => {
            setActionTarget({ object: menu.object, actionApiName });
            setMenu(null);
          }}
          onExportCsv={() => {
            exportCsv(objectApiName, columns, sortedObjects, objectView);
            setMenu(null);
          }}
          onClose={() => setMenu(null)}
        />
      ) : null}
      {actionTarget ? (
        <RowActionModal
          target={actionTarget}
          actionView={actionViewFor(props, actionTarget.actionApiName)}
          onApplied={() => {
            dispatch({ type: "bumpData" });
            setActionTarget(null);
          }}
          onClose={() => setActionTarget(null)}
        />
      ) : null}
    </>
  );
}

type CellMenu = {
  x: number;
  y: number;
  object: GenericObject;
  column: string;
  value: unknown;
} | null;

type ActionTarget = { object: GenericObject; actionApiName: string } | null;

/** 행 액션 intent 색·아이콘 (버튼 그룹과 동일 관례). */
function rowActionStyle(apiName: string): { color: string; icon: LucideIcon } {
  const name = apiName.toLowerCase();
  if (/approve|resolve|confirm|complete|activate|accept|assign/.test(name))
    return { color: "#238551", icon: Check };
  if (/reject|delete|cancel|remove|deactivate|purge|discard/.test(name))
    return { color: "#cd4246", icon: X };
  if (/adjust|edit|modify|update|change|revise/.test(name))
    return { color: "#c87619", icon: Pencil };
  return { color: "#2d72d2", icon: Play };
}

/** 표시 객체를 CSV로 내보내 다운로드. */
function exportCsv(
  objectApiName: string,
  columns: string[],
  objects: readonly GenericObject[],
  objectView: ReturnType<typeof objectViewFor>,
): void {
  const escape = (value: unknown): string => {
    const text = value === null || value === undefined ? "" : String(value);
    // Neutralize spreadsheet formula injection before quoting.
    const neutralized = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
    return /[",\n]/.test(neutralized)
      ? `"${neutralized.replace(/"/g, '""')}"`
      : neutralized;
  };
  const header = columns.map((name) =>
    escape(columnDisplayName(objectView, name)),
  );
  const rows = objects.map((object) =>
    columns.map((name) => escape(object.properties[name])).join(","),
  );
  const csv = [header.join(","), ...rows].join("\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${objectApiName}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** 객체 테이블 우클릭 컨텍스트 메뉴 (Palantir: CELL·ROW ACTIONS·TABLE EXPORT). */
function TableContextMenu({
  menu,
  objectView,
  rowActionApiNames,
  actionViews,
  onCopy,
  onRowAction,
  onExportCsv,
  onClose,
}: {
  menu: NonNullable<CellMenu>;
  objectView: ReturnType<typeof objectViewFor>;
  rowActionApiNames: readonly string[];
  actionViews: WidgetRuntimeProps["actionViews"];
  onCopy: (value: unknown) => void;
  onRowAction: (actionApiName: string) => void;
  onExportCsv: () => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  const cellLabel = columnDisplayName(objectView, menu.column);
  const cellText = formatCellValue(menu.value);
  // 화면 밖으로 넘치지 않도록 좌표 보정.
  const left = Math.min(menu.x, window.innerWidth - 236);
  const top = Math.min(menu.y, window.innerHeight - 320);

  return (
    <div
      className="fixed inset-0 z-50"
      onClick={onClose}
      onContextMenu={onClose}
    >
      <div
        className="absolute w-[224px] overflow-hidden rounded-md border border-[#d5dce1] bg-white py-1 shadow-lg"
        style={{ left, top }}
        onClick={(event) => event.stopPropagation()}
      >
        <MenuSectionLabel label="CELL" />
        <MenuItem
          icon={Copy}
          onClick={() => onCopy(menu.value)}
          label={
            <>
              복사{" "}
              <span className="text-[#8f99a8]">
                '{cellLabel}: {cellText}'
              </span>
            </>
          }
        />

        {rowActionApiNames.length > 0 ? (
          <>
            <MenuDivider />
            <MenuSectionLabel label="ROW ACTIONS" />
            {rowActionApiNames.map((apiName) => {
              const view = actionViews.find((item) => item.apiName === apiName);
              const style = rowActionStyle(apiName);
              return (
                <MenuItem
                  key={apiName}
                  icon={style.icon}
                  iconColor={style.color}
                  onClick={() => onRowAction(apiName)}
                  label={view?.displayName ?? apiName}
                />
              );
            })}
          </>
        ) : null}

        <MenuDivider />
        <MenuSectionLabel label="TABLE EXPORT" />
        <MenuItem
          icon={Download}
          onClick={onExportCsv}
          label="CSV로 내보내기"
        />
      </div>
    </div>
  );
}

function MenuSectionLabel({ label }: { label: string }) {
  return (
    <div className="px-3 pt-1.5 pb-1 text-[10px] font-semibold tracking-wide text-[#a7b1bd]">
      {label}
    </div>
  );
}

function MenuDivider() {
  return <div className="my-1 border-t border-[#eef1f4]" />;
}

function MenuItem({
  icon: Icon,
  iconColor,
  label,
  onClick,
}: {
  icon: LucideIcon;
  iconColor?: string;
  label: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] text-[#1c2127] hover:bg-[#f0f4f9]"
    >
      <Icon
        className="size-3.5 shrink-0"
        style={iconColor ? { color: iconColor } : undefined}
      />
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </button>
  );
}

/** 행 액션 실행 모달: 선택 행 + 액션에 대한 폼(멱등·낙관적 잠금). */
function RowActionModal({
  target,
  actionView,
  onApplied,
  onClose,
}: {
  target: NonNullable<ActionTarget>;
  actionView: ReturnType<typeof actionViewFor>;
  onApplied: () => void;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <button
        type="button"
        aria-label="닫기"
        onClick={onClose}
        className="absolute inset-0 bg-black/30"
      />
      <div className="relative z-10 w-full max-w-md overflow-hidden rounded-lg bg-white shadow-xl">
        <div className="flex h-11 items-center gap-2 border-b border-[#e4e9ed] px-4">
          <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-[#1c2127]">
            {actionView?.displayName ?? target.actionApiName}
          </span>
          <button
            type="button"
            aria-label="닫기"
            onClick={onClose}
            className="flex size-7 items-center justify-center rounded text-[#8f99a8] hover:bg-[#f0f2f5] hover:text-[#1c2127]"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className="p-4">
          {actionView ? (
            <RuntimeActionForm
              key={`${actionView.apiName}:${target.object.objectId}`}
              actionView={actionView}
              targetObject={target.object}
              onApplied={onApplied}
              onCancel={onClose}
            />
          ) : (
            <WidgetPlaceholder label="액션을 찾을 수 없습니다" />
          )}
        </div>
      </div>
    </div>
  );
}

function TableCheckbox({
  variant,
  onClick,
}: {
  variant: "checked" | "unchecked" | "indeterminate";
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label="선택"
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      className={cn(
        "flex size-4 items-center justify-center rounded-[3px] border",
        variant === "unchecked"
          ? "border-[#a7b1bd] bg-white hover:border-[#5f6b7c]"
          : "border-[#2d72d2] bg-[#2d72d2]",
      )}
    >
      {variant === "checked" ? (
        <Check className="size-3 text-white" strokeWidth={3} />
      ) : variant === "indeterminate" ? (
        <Minus className="size-3 text-white" strokeWidth={3} />
      ) : null}
    </button>
  );
}

/**
 * 객체 리스트 (Palantir Object List 클론): 아이콘+제목 + 속성 "라벨 • 값" 행 카드.
 * 범주형은 태그, 빈 값은 "값 없음" 이탤릭. 행 선택이 상세·액션을 구동한다.
 */
export function ObjectListWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { objects, isLoading } = useWidgetObjects(
    objectApiName,
    widget.config.variableFilters,
  );
  const objectView = objectViewFor(props, objectApiName);
  const state = useRuntimeState();
  const dispatch = useRuntimeDispatch();

  const displayProps = useMemo(() => {
    if (widget.config.propertyApiNames && widget.config.propertyApiNames.length)
      return widget.config.propertyApiNames;
    return (objectView?.properties ?? [])
      .filter((property) => !property.isPrimaryKey)
      .map((property) => property.apiName)
      .slice(0, 5);
  }, [objectView, widget.config.propertyApiNames]);

  if (!objectApiName) return MISSING_OBJECT;

  const tint = objectTypeColor(objectApiName);

  return (
    <WidgetFrame
      title={widget.config.title || objectView?.displayName || objectApiName}
      subtitle={`${objects.length.toLocaleString("en-US")}건`}
      className="min-h-[220px]"
      bodyClassName="overflow-auto divide-y divide-[#eef1f4]"
    >
      {objects.map((object) => {
        const isSelected = object.objectId === state.selectedObjectId;
        return (
          <button
            key={object.objectId}
            type="button"
            onClick={() =>
              dispatch({ type: "selectObject", objectId: object.objectId })
            }
            className={cn(
              "block w-full px-3 py-2.5 text-left hover:bg-[#f6f8fa]",
              isSelected && "bg-[#e8f0fb] hover:bg-[#e8f0fb]",
            )}
          >
            <div className="flex items-center gap-2">
              <span
                className="flex size-5 shrink-0 items-center justify-center rounded-[3px] text-[10px] font-bold text-white"
                style={{ background: tint }}
              >
                {object.objectType.slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-[#1c2127]">
                {objectTitleOf(object, objectView)}
              </span>
            </div>
            <dl className="mt-1.5 space-y-0.5 pl-7">
              {displayProps.map((name) => {
                const value = object.properties[name];
                const isEmpty =
                  value === null || value === undefined || value === "";
                return (
                  <div
                    key={name}
                    className="flex items-center gap-1.5 text-[11px]"
                  >
                    <dt className="shrink-0 text-[#5f6b7c]">
                      {columnDisplayName(objectView, name)}
                    </dt>
                    <span className="text-[#a7b1bd]">•</span>
                    <dd className="min-w-0 flex-1 truncate">
                      {isEmpty ? (
                        <span className="text-[#a7b1bd] italic">값 없음</span>
                      ) : isCategoricalColumn(name) &&
                        typeof value === "string" ? (
                        <StatusPill intent={statusIntentOf(value)}>
                          {value}
                        </StatusPill>
                      ) : (
                        <span className="text-[#1c2127]">
                          {formatCellValue(value)}
                        </span>
                      )}
                    </dd>
                  </div>
                );
              })}
            </dl>
          </button>
        );
      })}
      {objects.length === 0 ? (
        <p className="p-4 text-center text-[11px] text-muted-foreground">
          {isLoading ? "불러오는 중…" : "조건에 맞는 객체가 없습니다."}
        </p>
      ) : null}
    </WidgetFrame>
  );
}

function selectedObjectFrom(
  objects: readonly GenericObject[],
  selectedObjectId: string | null,
): GenericObject | null {
  if (!selectedObjectId) return null;
  return objects.find((object) => object.objectId === selectedObjectId) ?? null;
}

/** 객체 상세: 선택 객체의 속성을 key-value로. */
export function ObjectDetailWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { allObjects } = useWidgetObjects(objectApiName);
  const objectView = objectViewFor(props, objectApiName);
  const state = useRuntimeState();

  if (!objectApiName) return MISSING_OBJECT;

  const object = selectedObjectFrom(allObjects, state.selectedObjectId);
  const propertyNames =
    widget.config.propertyApiNames && widget.config.propertyApiNames.length > 0
      ? widget.config.propertyApiNames
      : (objectView?.properties.map((property) => property.apiName) ??
        (object ? Object.keys(object.properties) : []));

  return (
    <WidgetFrame
      title={widget.config.title || "객체 상세"}
      subtitle={object ? object.objectId : undefined}
      className="min-h-[220px]"
      bodyClassName="overflow-auto"
    >
      {!object ? (
        <WidgetPlaceholder
          label="객체를 선택하세요"
          hint="테이블·리스트에서 행을 클릭하면 속성이 표시됩니다."
        />
      ) : (
        <div>
          {/* 헤더: 아이콘 + 제목 + 타입 (Object View 스타일) */}
          <div className="flex items-center gap-3 border-b border-[#eef1f4] px-3 py-3">
            <span
              className="flex size-9 shrink-0 items-center justify-center rounded-md text-[15px] font-bold text-white"
              style={{ background: objectTypeColor(objectApiName) }}
            >
              {object.objectType.slice(0, 1).toUpperCase()}
            </span>
            <div className="min-w-0">
              <div className="truncate text-[14px] font-semibold text-[#1c2127]">
                {objectTitleOf(object, objectView)}
              </div>
              <div className="truncate text-[11px] text-[#8f99a8]">
                {objectView?.displayName ?? objectApiName}
              </div>
            </div>
          </div>

          {/* 핵심 지표 셀 (숫자 속성) */}
          {(() => {
            const numericProps = propertyNames.filter(
              (name) => typeof object.properties[name] === "number",
            );
            if (numericProps.length === 0) return null;
            return (
              <div className="flex divide-x divide-[#eef1f4] border-b border-[#eef1f4]">
                {numericProps.slice(0, 3).map((name) => (
                  <div key={name} className="min-w-0 flex-1 px-3 py-2">
                    <div className="truncate text-[10px] text-[#8f99a8]">
                      {columnDisplayName(objectView, name)}
                    </div>
                    <div className="text-[18px] font-bold text-[#1c2127] tabular-nums">
                      {formatCellValue(object.properties[name])}
                    </div>
                  </div>
                ))}
              </div>
            );
          })()}

          {/* 속성 목록 */}
          <dl className="divide-y divide-[#eef1f4]">
            {propertyNames.map((name) => {
              const value = object.properties[name];
              return (
                <div key={name} className="flex gap-3 px-3 py-1.5">
                  <dt className="w-32 shrink-0 text-[11px] text-muted-foreground">
                    {columnDisplayName(objectView, name)}
                  </dt>
                  <dd className="min-w-0 flex-1 text-[12px] text-[#1c2127]">
                    {isCategoricalColumn(name) && typeof value === "string" ? (
                      <StatusPill intent={statusIntentOf(value)}>
                        {value}
                      </StatusPill>
                    ) : (
                      <span
                        className={cn(
                          typeof value === "number" && "font-mono tabular-nums",
                        )}
                      >
                        {formatCellValue(value)}
                      </span>
                    )}
                  </dd>
                </div>
              );
            })}
          </dl>
        </div>
      )}
    </WidgetFrame>
  );
}

/** 객체 세트 제목: 현재 집합의 개수를 제목/카운트로. */
export function ObjectSetTitleWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { objects, allObjects } = useWidgetObjects(objectApiName);
  const objectView = objectViewFor(props, objectApiName);

  if (!objectApiName) return MISSING_OBJECT;

  const title = widget.config.title || objectView?.displayName || objectApiName;
  return (
    <div className="flex items-center gap-3 px-1">
      <span className="flex size-9 items-center justify-center rounded-md bg-[#2d72d2]/10 text-[15px] font-bold text-[#2d72d2]">
        {title.slice(0, 1).toUpperCase()}
      </span>
      <div className="min-w-0">
        <div className="truncate text-[15px] font-bold text-[#1c2127]">
          {title}
        </div>
        <div className="text-[11px] text-muted-foreground">
          {widget.config.text ? `${widget.config.text} · ` : ""}
          <span className="font-mono tabular-nums">
            {objects.length.toLocaleString("en-US")}
          </span>
          {objects.length !== allObjects.length ? (
            <span className="text-[#8f99a8]">
              {" "}
              / {allObjects.length.toLocaleString("en-US")}
            </span>
          ) : null}{" "}
          건
        </div>
      </div>
    </div>
  );
}

/** 링크: 선택 객체 타입의 관계(링크) 정의를 표시. */
export function LinksWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const objectView = objectViewFor(props, objectApiName);
  const { allObjects } = useWidgetObjects(objectApiName);
  const state = useRuntimeState();

  if (!objectApiName) return MISSING_OBJECT;

  const object = selectedObjectFrom(allObjects, state.selectedObjectId);
  const links = [
    ...(objectView?.linksFrom ?? []),
    ...(objectView?.linksTo ?? []),
  ];

  return (
    <WidgetFrame
      title={widget.config.title || "링크"}
      subtitle={object ? object.objectId : `${links.length}개 관계`}
      className="min-h-[160px]"
      bodyClassName="overflow-auto p-2 space-y-1"
    >
      {links.length === 0 ? (
        <WidgetPlaceholder label="정의된 링크가 없습니다" />
      ) : (
        links.map((link) => (
          <div
            key={`${link.apiName}-${link.toObjectType}`}
            className="flex items-center gap-2 rounded border border-[#e4e9ed] px-2.5 py-2"
          >
            <Link2 className="size-3.5 shrink-0 text-[#00847a]" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12px] font-medium text-[#1c2127]">
                {link.displayName || link.apiName}
              </span>
              <span className="flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
                {link.fromObjectType}
                <ArrowRight className="size-2.5" />
                {link.toObjectType}
              </span>
            </span>
            <StatusPill intent="neutral">{link.cardinality}</StatusPill>
          </div>
        ))
      )}
    </WidgetFrame>
  );
}
