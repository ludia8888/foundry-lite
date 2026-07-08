import {
  AppWindow,
  ArrowDown,
  ArrowUp,
  Box,
  Boxes,
  Cable,
  GitBranch,
  Layers,
  Link2,
  Package,
  RotateCcw,
  Search,
  Star,
  Table2,
} from "lucide-react";
import { useMemo, useState } from "react";

import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import type { ResourceKind, ResourceRow } from "./resource-model";
import { RESOURCE_KIND_META } from "./resource-model";

const KIND_ICONS: Record<ResourceKind, typeof Table2> = {
  dataset: Table2,
  source: Cable,
  pipeline_branch: GitBranch,
  object_type: Box,
  link_type: Link2,
  object_set: Layers,
  osdk_app: AppWindow,
  ontology: Boxes,
  registered: Package,
};

/** 리소스 종류별 아이콘 컬러 (Compass 리소스 아이콘 관례). */
const KIND_ICON_COLOR: Record<ResourceKind, string> = {
  dataset: "text-[#2D72D2]",
  source: "text-[#C87619]",
  pipeline_branch: "text-[#0F9960]",
  object_type: "text-[#7961DB]",
  link_type: "text-[#7961DB]",
  object_set: "text-[#2D72D2]",
  osdk_app: "text-[#00847A]",
  ontology: "text-[#7961DB]",
  registered: "text-[#5F6B7C]",
};

const STATUS_INTENTS: Record<string, StatusIntent> = {
  active: "success",
  open: "info",
  disabled: "warning",
  archived: "neutral",
  trashed: "danger",
};

export function resourceStatusIntent(status: string | null): StatusIntent {
  if (!status) return "neutral";
  return STATUS_INTENTS[status] ?? "neutral";
}

export function ResourceKindIcon({
  kind,
  className,
}: {
  kind: ResourceKind;
  className?: string;
}) {
  const Icon = KIND_ICONS[kind];
  return (
    <Icon className={cn("size-4 shrink-0", KIND_ICON_COLOR[kind], className)} />
  );
}

/** Compass식 친근한 날짜 표기 ("2026년 7월 6일 (월) 오후 4:23"). */
function formatFriendlyDate(iso: string | null): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type SortKey = "name" | "updatedAt";
type SortDir = "asc" | "desc";

const MIN_STRIPE_ROWS = 14;

interface ResourceTableProps {
  rows: ResourceRow[];
  selectedRid: string | null;
  onSelectRow: (row: ResourceRow) => void;
  pendingRids: ReadonlySet<string>;
  onToggleFavorite: (rid: string) => void;
  isTrashView: boolean;
  onRestore: (rid: string) => void;
  searchText: string;
  onSearchTextChange: (value: string) => void;
  kindFilter: ResourceKind | "all";
  onKindFilterChange: (value: ResourceKind | "all") => void;
  isFavoriteOnly: boolean;
  onToggleFavoriteOnly: () => void;
}

/** 메인 리소스 테이블 (Compass): 이름 | 마지막 업데이트 | 태그 + zebra 스트라이프. */
export function ResourceTable(props: ResourceTableProps) {
  const {
    rows,
    selectedRid,
    onSelectRow,
    pendingRids,
    onToggleFavorite,
    isTrashView,
    onRestore,
    searchText,
    onSearchTextChange,
    kindFilter,
    onKindFilterChange,
    isFavoriteOnly,
    onToggleFavoriteOnly,
  } = props;

  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const sortedRows = useMemo(() => {
    const factor = sortDir === "asc" ? 1 : -1;
    return [...rows].sort((left, right) => {
      const leftValue = sortKey === "name" ? left.name : (left.updatedAt ?? "");
      const rightValue =
        sortKey === "name" ? right.name : (right.updatedAt ?? "");
      return leftValue.localeCompare(rightValue) * factor;
    });
  }, [rows, sortKey, sortDir]);

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((previous) => (previous === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const fillerCount = Math.max(0, MIN_STRIPE_ROWS - sortedRows.length);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchText}
            onChange={(event) => onSearchTextChange(event.target.value)}
            placeholder="이름 또는 RID 검색"
            className="h-7 w-56 pl-7 text-[12px]"
          />
        </div>
        <Select
          value={kindFilter}
          onValueChange={(value) =>
            onKindFilterChange(value as ResourceKind | "all")
          }
        >
          <SelectTrigger size="sm" className="h-7 w-40 text-[12px]">
            <SelectValue placeholder="타입 전체" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">타입 전체</SelectItem>
            {Object.entries(RESOURCE_KIND_META).map(([kind, meta]) => (
              <SelectItem key={kind} value={kind}>
                {meta.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {!isTrashView ? (
          <Button
            size="sm"
            variant={isFavoriteOnly ? "secondary" : "outline"}
            className="h-7 px-2 text-[12px]"
            onClick={onToggleFavoriteOnly}
          >
            <Star
              className={cn(
                "size-3.5",
                isFavoriteOnly && "fill-warning text-warning",
              )}
            />
            즐겨찾기만
          </Button>
        ) : null}
        <span className="ml-auto font-mono text-[11px] text-muted-foreground">
          리소스 {rows.length}개
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto rounded border bg-card">
        <table className="w-full border-collapse text-[13px]">
          <thead className="sticky top-0 z-10 bg-card">
            <tr className="border-b">
              <SortHeader
                label="이름"
                sortKey="name"
                activeKey={sortKey}
                dir={sortDir}
                onSort={handleSort}
                className="w-1/2"
              />
              <SortHeader
                label="마지막 업데이트"
                sortKey="updatedAt"
                activeKey={sortKey}
                dir={sortDir}
                onSort={handleSort}
                className="w-64"
              />
              <th className="section-label h-8 px-3 text-left align-middle">
                태그
              </th>
              <th className="w-12" />
            </tr>
          </thead>
          <tbody>
            {sortedRows.length === 0 ? (
              <tr>
                <td
                  colSpan={4}
                  className="h-16 px-3 text-center text-xs text-muted-foreground"
                >
                  {isTrashView
                    ? "휴지통이 비어 있습니다."
                    : "조건에 맞는 리소스가 없습니다. 검색어나 폴더를 바꿔보세요."}
                </td>
              </tr>
            ) : (
              sortedRows.map((row, index) => (
                <ResourceRowView
                  key={row.rid}
                  row={row}
                  isSelected={row.rid === selectedRid}
                  isEven={index % 2 === 0}
                  isPending={pendingRids.has(row.rid)}
                  isTrashView={isTrashView}
                  onSelect={() => onSelectRow(row)}
                  onToggleFavorite={() => onToggleFavorite(row.rid)}
                  onRestore={() => onRestore(row.rid)}
                />
              ))
            )}
            {Array.from({ length: fillerCount }, (_, index) => (
              <tr
                key={`filler-${index}`}
                className={cn(
                  "h-8",
                  (sortedRows.length + index) % 2 === 1 && "bg-muted/30",
                )}
              >
                <td colSpan={4} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SortHeader({
  label,
  sortKey,
  activeKey,
  dir,
  onSort,
  className,
}: {
  label: string;
  sortKey: SortKey;
  activeKey: SortKey;
  dir: SortDir;
  onSort: (key: SortKey) => void;
  className?: string;
}) {
  const isActive = activeKey === sortKey;
  return (
    <th className={cn("h-8 px-3 text-left align-middle", className)}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className="section-label flex items-center gap-1 hover:text-foreground"
      >
        {label}
        {isActive ? (
          dir === "asc" ? (
            <ArrowUp className="size-3" />
          ) : (
            <ArrowDown className="size-3" />
          )
        ) : null}
      </button>
    </th>
  );
}

function ResourceRowView({
  row,
  isSelected,
  isEven,
  isPending,
  isTrashView,
  onSelect,
  onToggleFavorite,
  onRestore,
}: {
  row: ResourceRow;
  isSelected: boolean;
  isEven: boolean;
  isPending: boolean;
  isTrashView: boolean;
  onSelect: () => void;
  onToggleFavorite: () => void;
  onRestore: () => void;
}) {
  return (
    <tr
      onClick={onSelect}
      className={cn(
        "group cursor-pointer border-b border-border/50 last:border-b-0",
        !isEven && "bg-muted/30",
        isSelected ? "bg-accent hover:bg-accent" : "hover:bg-primary/5",
      )}
    >
      <td className="h-8 px-3 align-middle whitespace-nowrap">
        <span className="flex items-center gap-2">
          <ResourceKindIcon kind={row.kind} />
          <span className="max-w-[320px] truncate font-medium">{row.name}</span>
        </span>
      </td>
      <td className="h-8 px-3 align-middle whitespace-nowrap text-muted-foreground">
        {formatFriendlyDate(row.updatedAt)}
      </td>
      <td className="h-8 px-3 align-middle whitespace-nowrap">
        <span className="flex items-center gap-1.5">
          <StatusPill intent="neutral">
            {RESOURCE_KIND_META[row.kind].label}
          </StatusPill>
          {row.status && row.status !== "active" ? (
            <StatusPill intent={resourceStatusIntent(row.status)}>
              {row.status}
            </StatusPill>
          ) : null}
        </span>
      </td>
      <td className="h-8 px-2 align-middle text-center">
        {isTrashView ? (
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            disabled={isPending}
            onClick={(event) => {
              event.stopPropagation();
              onRestore();
            }}
          >
            <RotateCcw className="size-3" />
            복원
          </Button>
        ) : (
          <button
            type="button"
            aria-label={row.isFavorite ? "즐겨찾기 해제" : "즐겨찾기 추가"}
            disabled={row.origin === "surface" || isPending}
            title={
              row.origin === "surface"
                ? "카탈로그 동기화 후 사용할 수 있습니다"
                : undefined
            }
            className={cn(
              "inline-flex size-6 items-center justify-center rounded hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent",
              !row.isFavorite && "opacity-0 group-hover:opacity-100",
            )}
            onClick={(event) => {
              event.stopPropagation();
              onToggleFavorite();
            }}
          >
            <Star
              className={cn(
                "size-3.5",
                row.isFavorite
                  ? "fill-warning text-warning"
                  : "text-muted-foreground/50",
              )}
            />
          </button>
        )}
      </td>
    </tr>
  );
}
