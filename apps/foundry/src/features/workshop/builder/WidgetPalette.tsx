import { Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

import type { WidgetKind } from "../lib/app-model";
import {
  WIDGET_CATEGORIES,
  WIDGET_LIST,
  type WidgetCategory,
} from "../lib/widget-catalog";
import { WidgetThumb } from "./WidgetThumb";

type TabId = "all" | WidgetCategory;

/**
 * 위젯 셀렉터 (Palantir Add widget 클론): 검색 + 카테고리 탭 +
 * 미리보기 썸네일 카드 그리드 모달.
 */
export function WidgetPalette({
  onPick,
  trigger,
}: {
  onPick: (kind: WidgetKind) => void;
  trigger: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<TabId>("all");

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    return WIDGET_LIST.filter((def) => {
      if (tab !== "all" && def.category !== tab) return false;
      if (!q) return true;
      return (
        def.label.toLowerCase().includes(q) ||
        def.description.toLowerCase().includes(q)
      );
    });
  }, [query, tab]);

  const pick = (kind: WidgetKind) => {
    onPick(kind);
    setOpen(false);
    setQuery("");
    setTab("all");
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent
        showCloseButton={false}
        className="max-h-[80vh] max-w-3xl gap-0 overflow-hidden p-0"
      >
        <DialogTitle className="sr-only">위젯 추가</DialogTitle>
        <div className="flex items-center gap-2 border-b border-[#e4e9ed] px-3 py-2.5">
          <Search className="size-4 shrink-0 text-[#8f99a8]" />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="위젯 검색..."
            className="min-w-0 flex-1 bg-transparent text-[14px] text-[#1c2127] outline-none placeholder:text-[#a7b1bd]"
          />
          <button
            type="button"
            aria-label="닫기"
            onClick={() => setOpen(false)}
            className="flex size-6 items-center justify-center rounded text-[#8f99a8] hover:bg-[#f0f2f5]"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="flex items-center gap-1 border-b border-[#e4e9ed] px-3">
          <Tab id="all" activeTab={tab} onSelect={setTab} label="전체" />
          {WIDGET_CATEGORIES.map((category) => (
            <Tab
              key={category.id}
              id={category.id}
              activeTab={tab}
              onSelect={setTab}
              label={category.label}
            />
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-auto bg-[#fafbfc] p-3">
          {query.trim() ? null : (
            <div className="px-1 pb-2 text-[11px] font-medium text-[#8f99a8]">
              추천
            </div>
          )}
          {results.length === 0 ? (
            <p className="px-1 py-6 text-center text-[12px] text-[#8f99a8]">
              일치하는 위젯이 없습니다.
            </p>
          ) : (
            <div className="grid grid-cols-3 gap-3">
              {results.map((def) => {
                const Icon = def.icon;
                return (
                  <button
                    key={def.kind}
                    type="button"
                    onClick={() => pick(def.kind)}
                    className="group flex flex-col overflow-hidden rounded-lg border border-[#e4e9ed] bg-white text-left transition hover:border-[#2d72d2] hover:shadow-sm"
                  >
                    <div className="relative flex h-[120px] items-center justify-center overflow-hidden bg-[#f4f6f8] p-3">
                      <WidgetThumb kind={def.kind} />
                      <span className="absolute top-2 right-2 flex size-6 items-center justify-center rounded-full border border-[#c9d4e3] bg-white text-[#7961db]">
                        <Icon className="size-3" />
                      </span>
                    </div>
                    <div className="border-t border-[#eef1f4] p-2.5">
                      <div className="text-[13px] font-semibold text-[#1c2127]">
                        {def.label}
                      </div>
                      <div className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-[#5f6b7c]">
                        {def.description}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Tab({
  id,
  activeTab,
  onSelect,
  label,
}: {
  id: TabId;
  activeTab: TabId;
  onSelect: (id: TabId) => void;
  label: string;
}) {
  const isActive = activeTab === id;
  return (
    <button
      type="button"
      onClick={() => onSelect(id)}
      className={cn(
        "-mb-px border-b-2 px-2.5 py-2 text-[12px] font-medium whitespace-nowrap",
        isActive
          ? "border-[#2d72d2] text-[#215db0]"
          : "border-transparent text-[#5f6b7c] hover:text-[#1c2127]",
      )}
    >
      {label}
    </button>
  );
}
