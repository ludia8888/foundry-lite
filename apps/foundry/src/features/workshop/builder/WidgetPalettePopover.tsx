import { Check } from "lucide-react";
import { useState } from "react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import {
  WIDGET_PALETTE,
  type PaletteItem,
  type WidgetKind,
} from "../lib/app-model";

interface WidgetPalettePopoverProps {
  children: React.ReactNode;
  onSelect: (kind: WidgetKind) => void;
}

function PaletteRow({
  item,
  onSelect,
}: {
  item: PaletteItem;
  onSelect: (kind: WidgetKind) => void;
}) {
  return (
    <button
      type="button"
      disabled={!item.isAvailable}
      onClick={() => item.isAvailable && onSelect(item.kind as WidgetKind)}
      className={cn(
        "flex w-full items-start gap-2 rounded px-2 py-1.5 text-left",
        item.isAvailable
          ? "hover:bg-[#2f343c]"
          : "cursor-not-allowed opacity-70",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="text-[13px] font-medium text-white">
            {item.label}
          </span>
          {!item.isAvailable ? (
            <span className="rounded bg-[#4a5058] px-1 py-px text-[9px] font-semibold tracking-wide text-[#c1c8cf] uppercase">
              future
            </span>
          ) : null}
        </div>
        <div className="truncate text-[11px] text-[#8f99a8]">
          {item.description}
        </div>
      </div>
      {item.isAvailable ? (
        <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-[#2b9f6c]">
          <Check className="size-2.5 text-white" strokeWidth={3} />
        </span>
      ) : null}
    </button>
  );
}

/** '위젯 추가' 팔레트: 다크 팝오버 (CORE WIDGETS / WIDGETS 그룹, future 배지). */
export function WidgetPalettePopover({
  children,
  onSelect,
}: WidgetPalettePopoverProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");

  const filtered = WIDGET_PALETTE.filter((item) =>
    item.label.toLowerCase().includes(search.toLowerCase()),
  );
  const coreItems = filtered.filter((item) => item.group === "core");
  const widgetItems = filtered.filter((item) => item.group === "widget");

  const handleSelect = (kind: WidgetKind) => {
    onSelect(kind);
    setIsOpen(false);
    setSearch("");
  };

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent
        align="center"
        className="w-72 rounded-md border-none bg-[#252a31] p-2 text-white shadow-xl"
      >
        <div className="px-1 pb-1.5">
          <div className="text-[13px] font-semibold text-white">새 위젯</div>
          <div className="text-[11px] text-[#8f99a8]">
            이 섹션에 위젯을 하나 추가합니다.
          </div>
        </div>
        <input
          autoFocus
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="위젯 검색"
          className="mb-2 h-7 w-full rounded border border-[#3a4048] bg-[#1c2127] px-2 text-[12px] text-white placeholder:text-[#6b7480] focus:border-[#2d72d2] focus:outline-none"
        />
        <div className="max-h-72 space-y-2 overflow-auto">
          {coreItems.length > 0 ? (
            <div>
              <div className="px-2 py-1 text-[10px] font-semibold tracking-wide text-[#6b7480] uppercase">
                Core widgets
              </div>
              {coreItems.map((item) => (
                <PaletteRow
                  key={item.kind}
                  item={item}
                  onSelect={handleSelect}
                />
              ))}
            </div>
          ) : null}
          {widgetItems.length > 0 ? (
            <div>
              <div className="px-2 py-1 text-[10px] font-semibold tracking-wide text-[#6b7480] uppercase">
                Widgets
              </div>
              {widgetItems.map((item) => (
                <PaletteRow
                  key={item.kind}
                  item={item}
                  onSelect={handleSelect}
                />
              ))}
            </div>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
