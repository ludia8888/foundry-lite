import type { LucideIcon } from "lucide-react";
import {
  ArrowLeftRight,
  Box,
  Eye,
  EyeOff,
  FunctionSquare,
  Layers,
  ListFilter,
  PlusCircle,
  Settings,
  Zap,
} from "lucide-react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

import type {
  OntologyResourceKind,
  OntologyResourceRow,
} from "../lib/ontology-view";
import {
  ontologyResourceRowKey,
  splitBySearchMatch,
} from "../lib/ontology-view";

/** 검색어와 일치하는 부분을 노란 <mark>로 강조해 렌더한다. */
function HighlightedText({ text, search }: { text: string; search: string }) {
  const segments = splitBySearchMatch(text, search);
  return (
    <>
      {segments.map((segment, index) =>
        segment.isMatch ? (
          <mark
            key={index}
            className="rounded-[2px] bg-[#f8eac8] px-0 text-inherit shadow-[inset_0_-1.5px_0_#c79637]"
          >
            {segment.text}
          </mark>
        ) : (
          <span key={index}>{segment.text}</span>
        ),
      )}
    </>
  );
}

const KIND_ICONS: Record<OntologyResourceKind, LucideIcon> = {
  objectType: Box,
  linkType: ArrowLeftRight,
  actionType: Zap,
  interface: Layers,
  functionType: FunctionSquare,
};

const KIND_ICON_CLASSES: Record<OntologyResourceKind, string> = {
  objectType: "bg-primary",
  linkType: "bg-success",
  actionType: "bg-warning",
  interface: "bg-primary/70",
  functionType: "bg-muted-foreground",
};

export function ResourceKindIcon({
  kind,
  className,
}: {
  kind: OntologyResourceKind;
  className?: string;
}) {
  const Icon = KIND_ICONS[kind];
  return (
    <span
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded text-white",
        KIND_ICON_CLASSES[kind],
        className,
      )}
    >
      <Icon className="size-3.5" />
    </span>
  );
}

/** 상태 필: Experimental 연오렌지 / Active 연회색 (레퍼런스 컬러). */
function ResourceStatusPill({ status }: { status: "active" | "experimental" }) {
  return status === "experimental" ? (
    <span className="inline-flex items-center rounded bg-[#f8f1ea] px-2 py-0.5 text-xs whitespace-nowrap text-[#8b5923]">
      실험적
    </span>
  ) : (
    <span className="inline-flex items-center rounded bg-[#eff0f2] px-2 py-0.5 text-xs whitespace-nowrap text-[#404854]">
      활성
    </span>
  );
}

/** 공개 범위 필: Normal 연파랑 + 눈 아이콘 / 제한 연오렌지. */
function ResourceVisibilityPill({ isRestricted }: { isRestricted: boolean }) {
  return isRestricted ? (
    <span className="inline-flex items-center gap-1 rounded bg-[#f8f1ea] px-2 py-0.5 text-xs whitespace-nowrap text-[#8b5923]">
      <EyeOff className="size-3" />
      제한
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded bg-[#ecf0fa] px-2 py-0.5 text-xs whitespace-nowrap text-[#325caa]">
      <Eye className="size-3" />
      보통
    </span>
  );
}

interface ResourceTableProps {
  sectionLabel: string;
  rows: readonly OntologyResourceRow[];
  /** 활성 검색어 — 매치 텍스트를 <mark>로 하이라이트한다. */
  search?: string;
  isLoading: boolean;
  error: unknown;
  selectedKey: string | null;
  checkedKeys: ReadonlySet<string>;
  onRetry: () => void;
  onSelectRow: (row: OntologyResourceRow) => void;
  onToggleCheck: (key: string) => void;
  onToggleAllChecks: (isChecked: boolean) => void;
  onRequestNew: () => void;
  className?: string;
}

/** 메인 테이블 카드: 타이틀 툴바 + 대문자 헤더(이름/상태/공개 범위/이슈) + 체크박스 행. */
export function ResourceTable({
  sectionLabel,
  rows,
  search = "",
  isLoading,
  error,
  selectedKey,
  checkedKeys,
  onRetry,
  onSelectRow,
  onToggleCheck,
  onToggleAllChecks,
  onRequestNew,
  className,
}: ResourceTableProps) {
  if (isLoading) {
    return <LoadingState className={className} rowCount={8} />;
  }
  if (error) {
    return <ErrorState className={className} error={error} onRetry={onRetry} />;
  }

  const isAllChecked = rows.length > 0 && checkedKeys.size === rows.length;

  return (
    <div className={cn("overflow-hidden rounded border bg-card", className)}>
      <div className="flex h-12 items-stretch">
        <div className="flex min-w-0 flex-1 items-center gap-3 px-3">
          <Checkbox
            checked={isAllChecked}
            onCheckedChange={(value) => onToggleAllChecks(value === true)}
            aria-label={`${sectionLabel} 전체 선택`}
          />
          <span className="truncate text-[13px] font-semibold">
            {sectionLabel}{" "}
            <span className="font-normal text-muted-foreground">
              ({rows.length})
            </span>
          </span>
          {checkedKeys.size > 0 ? (
            <span className="text-[11px] text-primary">
              {checkedKeys.size}개 선택됨
            </span>
          ) : null}
          <button
            type="button"
            onClick={onRequestNew}
            className="ml-auto flex shrink-0 items-center gap-1.5 rounded px-2 py-1 text-xs font-medium text-primary hover:bg-primary/10"
          >
            <PlusCircle className="size-3.5" />새 {sectionLabel}
          </button>
        </div>
        <div className="flex w-11 items-center justify-center border-l">
          <ListFilter className="size-4 text-muted-foreground" />
        </div>
      </div>
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="border-y bg-[#f6f7f9]">
            <th className="w-10 px-3" />
            <th className="h-9 px-1 text-left align-middle text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">
              이름
            </th>
            <th className="h-9 w-[18%] border-l px-3 text-left align-middle text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">
              상태
            </th>
            <th className="h-9 w-[18%] border-l px-3 text-left align-middle text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">
              공개 범위
            </th>
            <th className="h-9 w-[22%] border-l px-3 text-left align-middle text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">
              이슈
            </th>
            <th className="w-11 px-0 text-center align-middle">
              <Settings className="inline size-3.5 text-muted-foreground" />
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={6} className="p-3">
                <EmptyState
                  title={`${sectionLabel}이(가) 없습니다`}
                  description="브랜치를 만든 뒤 하단 YAML 드래프트 편집기에서 정의를 추가하고 검증하세요."
                  className="border-0"
                />
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              const key = ontologyResourceRowKey(row);
              const isSelected = selectedKey === key;
              return (
                <tr
                  key={key}
                  onClick={() => onSelectRow(row)}
                  className={cn(
                    "h-14 cursor-pointer border-b last:border-b-0",
                    isSelected
                      ? "bg-accent hover:bg-accent"
                      : "hover:bg-muted/40",
                  )}
                >
                  <td className="w-10 px-3 align-middle">
                    <span onClick={(event) => event.stopPropagation()}>
                      <Checkbox
                        checked={checkedKeys.has(key)}
                        onCheckedChange={() => onToggleCheck(key)}
                        aria-label={`${row.displayName} 선택`}
                      />
                    </span>
                  </td>
                  <td className="px-1 align-middle">
                    <span className="flex items-center gap-3">
                      <ResourceKindIcon kind={row.kind} />
                      <span className="min-w-0">
                        <span className="block truncate text-[13px] font-medium">
                          <HighlightedText
                            text={row.displayName}
                            search={search}
                          />
                        </span>
                        {row.subtitle ? (
                          <span className="block truncate text-xs text-muted-foreground">
                            <HighlightedText
                              text={row.subtitle}
                              search={search}
                            />
                          </span>
                        ) : null}
                      </span>
                    </span>
                  </td>
                  <td className="w-[18%] px-3 align-middle">
                    <ResourceStatusPill status={row.status} />
                  </td>
                  <td className="w-[18%] px-3 align-middle">
                    <ResourceVisibilityPill isRestricted={row.isRestricted} />
                  </td>
                  <td className="w-[22%] px-3 align-middle" />
                  <td className="w-11 px-0 align-middle" />
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
