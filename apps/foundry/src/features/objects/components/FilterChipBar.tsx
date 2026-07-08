import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";
import {
  ChevronRight,
  CircleHelp,
  Link2,
  Plus,
  Search,
  Users,
  X,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import {
  createPillId,
  FILTER_OPS,
  objectTypeIconClass,
  pillLabelParts,
  type FilterOp,
  type FilterPill,
  type LinkOrigin,
} from "../lib/explorer-model";

interface FilterChipBarProps {
  objectView: FoundryLiteOntologyObjectView | null;
  explorationName: string;
  linkOrigin: LinkOrigin | null;
  originView: FoundryLiteOntologyObjectView | null;
  pills: FilterPill[];
  appliedSearch: string;
  onAddPill: (pill: FilterPill) => void;
  onRemovePill: (pillId: string) => void;
  onClearAll: () => void;
  onApplySearch: (text: string) => void;
  onOpenOrigin: (typeName: string) => void;
  onOpenSaveObjectSet: () => void;
}

function AddFilterPopover({
  objectView,
  onAddPill,
}: Pick<FilterChipBarProps, "objectView" | "onAddPill">) {
  const [isOpen, setIsOpen] = useState(false);
  const [property, setProperty] = useState("");
  const [op, setOp] = useState<FilterOp>("eq");
  const [value, setValue] = useState("");
  const properties = objectView?.properties ?? [];
  const canAdd = property.length > 0 && value.length > 0;

  const handleAdd = () => {
    if (!canAdd) return;
    const dataType =
      properties.find((candidate) => candidate.apiName === property)
        ?.dataType ?? "string";
    onAddPill({ id: createPillId(), property, op, value, dataType });
    setValue("");
    setIsOpen(false);
  };

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex h-7 shrink-0 items-center gap-1 rounded border border-[#accdea] bg-white px-2 text-[12px] font-medium text-[#215db0] hover:bg-[#edf4fa]"
        >
          <Plus className="size-3.5" />
          필터 추가
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 space-y-2 p-3">
        <div className="section-label">필터 조건</div>
        <Select value={property} onValueChange={setProperty}>
          <SelectTrigger size="sm" className="w-full">
            <SelectValue placeholder="속성 선택" />
          </SelectTrigger>
          <SelectContent>
            {properties.map((candidate) => (
              <SelectItem key={candidate.apiName} value={candidate.apiName}>
                {candidate.displayName}
                <span className="font-mono text-[10px] text-muted-foreground">
                  {candidate.dataType}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={op} onValueChange={(next) => setOp(next as FilterOp)}>
          <SelectTrigger size="sm" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FILTER_OPS.map((candidate) => (
              <SelectItem key={candidate.op} value={candidate.op}>
                {candidate.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          className="h-8 text-xs"
          placeholder="비교 값"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") handleAdd();
          }}
        />
        <Button
          size="sm"
          className="w-full"
          disabled={!canAdd}
          onClick={handleAdd}
        >
          조건 추가
        </Button>
      </PopoverContent>
    </Popover>
  );
}

function FilterPillChip({
  pill,
  onRemove,
}: {
  pill: FilterPill;
  onRemove: () => void;
}) {
  const parts = pillLabelParts(pill);
  return (
    <span className="flex h-7 shrink-0 items-center gap-1.5 rounded bg-[#e4eaf0] px-2.5 text-[12px] text-[#383e47]">
      <span className="whitespace-nowrap">
        {parts.prefix}
        <span className="font-semibold">{parts.value}</span>
        {parts.suffix}
      </span>
      <button
        type="button"
        aria-label="필터 제거"
        className="text-[#8b98a6] hover:text-foreground"
        onClick={onRemove}
      >
        <X className="size-3.5" />
      </button>
    </span>
  );
}

/** 필터 바 (2행): 탐색 아이덴티티 + 자연어 필터 필 + 링크 순회 필 + 인라인 검색 + 저장. */
export function FilterChipBar({
  objectView,
  explorationName,
  linkOrigin,
  originView,
  pills,
  appliedSearch,
  onAddPill,
  onRemovePill,
  onClearAll,
  onApplySearch,
  onOpenOrigin,
  onOpenSaveObjectSet,
}: FilterChipBarProps) {
  const [searchDraft, setSearchDraft] = useState("");
  const hasConditions = pills.length > 0 || appliedSearch.length > 0;

  return (
    <div className="flex h-14 shrink-0 items-center gap-3 border-b border-[#d5dce1] bg-white px-3">
      <button
        type="button"
        aria-label="Object Explorer 홈"
        className="flex size-9 shrink-0 items-center justify-center rounded bg-[#ece9fb] hover:bg-[#e0dbf7]"
      >
        <Search className="size-4.5 text-[#7961db]" />
      </button>
      <span className="h-9 w-px shrink-0 bg-[#e4e9ed]" />
      {objectView ? (
        <div className="flex min-w-0 shrink-0 items-center gap-2.5">
          <span
            className={cn(
              "flex size-9 shrink-0 items-center justify-center rounded text-[15px] font-bold text-white",
              objectTypeIconClass(objectView.apiName),
            )}
          >
            {objectView.apiName.slice(0, 1).toUpperCase()}
          </span>
          <span className="min-w-0">
            <span className="block max-w-44 truncate text-[14px] leading-tight font-bold text-[#1c2127]">
              {explorationName}
            </span>
            <span className="block max-w-44 truncate text-[12px] leading-tight text-[#5f6b7c]">
              {objectView.displayName}
            </span>
          </span>
        </div>
      ) : null}
      <div className="flex h-10 min-w-0 flex-1 items-center gap-1.5 overflow-x-auto rounded border border-[#d3d8de] bg-[#f6f8fa] px-2">
        {pills.map((pill) => (
          <FilterPillChip
            key={pill.id}
            pill={pill}
            onRemove={() => onRemovePill(pill.id)}
          />
        ))}
        {linkOrigin ? (
          <button
            type="button"
            className="flex h-7 shrink-0 items-center gap-1.5 rounded bg-[#e4eaf0] px-2.5 text-[12px] text-[#383e47] hover:bg-[#dae2ea]"
            title={`${linkOrigin.linkApiName} 링크로 순회한 탐색`}
            onClick={() => onOpenOrigin(linkOrigin.fromTypeName)}
          >
            <Link2 className="size-3.5 text-[#5f6b7c]" />
            <span
              className={cn(
                "flex size-4 items-center justify-center rounded-[3px] text-[9px] font-bold text-white",
                objectTypeIconClass(linkOrigin.fromTypeName),
              )}
            >
              {linkOrigin.fromTypeName.slice(0, 1).toUpperCase()}
            </span>
            <span className="font-semibold">
              {originView?.displayName ?? linkOrigin.fromTypeName}
            </span>
            <ChevronRight className="size-3.5 text-[#5f6b7c]" />
          </button>
        ) : null}
        {appliedSearch ? (
          <span className="flex h-7 shrink-0 items-center gap-1.5 rounded bg-[#e4eaf0] px-2.5 text-[12px] text-[#383e47]">
            <span className="whitespace-nowrap">
              <span className="font-semibold">"{appliedSearch}"</span> 검색
            </span>
            <button
              type="button"
              aria-label="검색 제거"
              className="text-[#8b98a6] hover:text-foreground"
              onClick={() => onApplySearch("")}
            >
              <X className="size-3.5" />
            </button>
          </span>
        ) : null}
        <AddFilterPopover objectView={objectView} onAddPill={onAddPill} />
        <Input
          className="h-8 min-w-44 flex-1 rounded-none border-none bg-transparent px-1.5 text-[13px] shadow-none placeholder:text-[#8b98a6] focus-visible:ring-0 dark:bg-transparent"
          placeholder="속성을 검색해 차트나 필터 추가..."
          value={searchDraft}
          onChange={(event) => setSearchDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            onApplySearch(searchDraft.trim());
            setSearchDraft("");
          }}
        />
        {hasConditions ? (
          <button
            type="button"
            className="shrink-0 text-[13px] text-[#404854] hover:underline"
            onClick={onClearAll}
          >
            모두 지우기
          </button>
        ) : null}
        <CircleHelp
          className="size-4 shrink-0 text-[#5f6b7c]"
          aria-label="필터 도움말"
        />
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          disabled
          title="공유 · 준비 중"
          className="flex h-8 items-center gap-1.5 px-1.5 text-[13px] font-medium text-[#404854] disabled:cursor-not-allowed"
        >
          <Users className="size-4" />
          공유
        </button>
        <button
          type="button"
          disabled={!objectView}
          className="h-8 rounded bg-[#2d72d2] px-4 text-[13px] font-semibold text-white hover:bg-[#215db0] disabled:bg-[#9bbddb]"
          onClick={onOpenSaveObjectSet}
        >
          저장
        </button>
      </div>
    </div>
  );
}
