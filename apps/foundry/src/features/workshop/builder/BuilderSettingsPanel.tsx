import { Check } from "lucide-react";

import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

import {
  BACKGROUND_SWATCHES,
  type AppLayoutDirection,
  type AppPage,
} from "../lib/app-model";

interface BuilderSettingsPanelProps {
  page: AppPage;
  onChange: (patch: Partial<AppPage>) => void;
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] font-semibold tracking-wide text-[#5f6b7c] uppercase">
      {children}
    </div>
  );
}

/** 우측 설정 패널: PAGE NAME / PAGE ID / MODULE DEFAULT PAGE / FORMATTING / LAYOUT. */
export function BuilderSettingsPanel({
  page,
  onChange,
}: BuilderSettingsPanelProps) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-[#d5dce1] px-3">
        <span className="text-[13px] font-semibold text-[#1c2127]">페이지</span>
        <span className="text-[10px] font-semibold tracking-wide text-[#8f99a8] uppercase">
          Page
        </span>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3">
        <div className="space-y-1.5">
          <SectionLabel>Page name</SectionLabel>
          <Input
            aria-label="페이지 이름"
            className="h-8 text-[13px]"
            value={page.name}
            onChange={(event) => onChange({ name: event.target.value })}
          />
        </div>

        <div className="space-y-1.5">
          <SectionLabel>Page id (선택)</SectionLabel>
          <Input
            className="h-8 font-mono text-[12px]"
            value={page.pageId}
            onChange={(event) => onChange({ pageId: event.target.value })}
          />
        </div>

        <div className="space-y-1.5">
          <SectionLabel>Module default page</SectionLabel>
          <button
            type="button"
            onClick={() => onChange({ isDefault: !page.isDefault })}
            className={cn(
              "flex w-full items-center gap-2 rounded border px-2 py-1.5 text-left text-[12px]",
              page.isDefault
                ? "border-[#2b9f6c]/40 bg-[#eaf6ee] text-[#1c2127]"
                : "border-[#d5dce1] text-[#5f6b7c] hover:bg-muted/50",
            )}
          >
            <span
              className={cn(
                "flex size-4 items-center justify-center rounded-full",
                page.isDefault
                  ? "bg-[#2b9f6c]"
                  : "border border-[#c1c8cf] bg-white",
              )}
            >
              {page.isDefault ? (
                <Check className="size-2.5 text-white" strokeWidth={3} />
              ) : null}
            </span>
            현재 페이지가 이 모듈의 기본 페이지입니다
          </button>
        </div>

        <div className="space-y-2 border-t border-[#e4e9ed] pt-3">
          <SectionLabel>Formatting</SectionLabel>
          <div className="space-y-1.5">
            <div className="text-[11px] text-[#5f6b7c]">배경 색상</div>
            <div className="flex items-center gap-1.5">
              {BACKGROUND_SWATCHES.map((swatch) => {
                const isSelected = page.backgroundColor === swatch.value;
                return (
                  <button
                    key={swatch.id}
                    type="button"
                    aria-label={swatch.label}
                    title={swatch.label}
                    onClick={() => onChange({ backgroundColor: swatch.value })}
                    className={cn(
                      "relative size-7 rounded border",
                      isSelected
                        ? "border-[#2d72d2] ring-1 ring-[#2d72d2]"
                        : "border-[#d5dce1]",
                    )}
                    style={{
                      backgroundColor:
                        swatch.value === "transparent"
                          ? "#ffffff"
                          : swatch.value,
                    }}
                  >
                    {swatch.value === "transparent" ? (
                      <span className="absolute inset-0 flex items-center justify-center text-[#c1c8cf]">
                        <span className="h-4 w-px rotate-45 bg-[#d5322f]" />
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <div className="space-y-2 border-t border-[#e4e9ed] pt-3">
          <SectionLabel>Layout</SectionLabel>
          <div className="text-[11px] text-[#5f6b7c]">레이아웃 방향</div>
          <ToggleGroup
            type="single"
            value={page.layoutDirection}
            onValueChange={(value) => {
              if (value)
                onChange({ layoutDirection: value as AppLayoutDirection });
            }}
            className="grid grid-cols-2 gap-0 rounded border border-[#d5dce1] p-0.5"
          >
            <ToggleGroupItem
              value="columns"
              className="h-7 rounded text-[12px] data-[state=on]:bg-[#e8f0fb] data-[state=on]:text-[#215db0]"
            >
              열
            </ToggleGroupItem>
            <ToggleGroupItem
              value="rows"
              className="h-7 rounded text-[12px] data-[state=on]:bg-[#e8f0fb] data-[state=on]:text-[#215db0]"
            >
              행
            </ToggleGroupItem>
          </ToggleGroup>
        </div>
      </div>
    </div>
  );
}
