import { Check, LayoutPanelTop, PanelLeft } from "lucide-react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import type {
  AppDefinition,
  AppDensity,
  AppNavigation,
  AppPageWidth,
  AppThemePreset,
} from "../lib/app-model";
import { setAppShell, setAppTheme } from "../lib/app-edit";

interface AppAppearancePanelProps {
  definition: AppDefinition;
  onChange: (definition: AppDefinition) => void;
}

const PRESETS: ReadonlyArray<{
  id: AppThemePreset;
  label: string;
  color: string;
  ink: string;
}> = [
  { id: "ocean", label: "오션", color: "#0b7285", ink: "#0d2b36" },
  { id: "indigo", label: "인디고", color: "#4f46e5", ink: "#1e1b4b" },
  { id: "emerald", label: "에메랄드", color: "#087f5b", ink: "#12372a" },
  { id: "amber", label: "앰버", color: "#b45309", ink: "#422006" },
  { id: "graphite", label: "그래파이트", color: "#475569", ink: "#111827" },
];

export function AppAppearancePanel({
  definition,
  onChange,
}: AppAppearancePanelProps) {
  const patchTheme = (patch: Parameters<typeof setAppTheme>[1]) =>
    onChange(setAppTheme(definition, patch));
  const patchShell = (patch: Parameters<typeof setAppShell>[1]) =>
    onChange(setAppShell(definition, patch));

  return (
    <aside className="flex h-full min-h-0 flex-col bg-white">
      <div className="border-b border-[#d5dce1] px-4 py-3">
        <div className="text-[13px] font-semibold text-[#1c2127]">
          앱 디자인과 탐색
        </div>
        <div className="mt-1 text-[11px] leading-4 text-[#738091]">
          GPT 미리보기와 외부 SaaS가 함께 사용하는 디자인입니다.
        </div>
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-auto p-4">
        <Control label="서비스 이름">
          <Input
            className="h-8 text-[12px]"
            value={definition.theme.brandName}
            onChange={(event) => patchTheme({ brandName: event.target.value })}
          />
        </Control>
        <Control label="로고 문자">
          <Input
            className="h-8 text-[12px]"
            maxLength={3}
            value={definition.theme.logoText}
            onChange={(event) => patchTheme({ logoText: event.target.value })}
          />
        </Control>
        <Control label="색상 체계">
          <div className="grid grid-cols-5 gap-1.5">
            {PRESETS.map((preset) => (
              <button
                key={preset.id}
                type="button"
                aria-label={preset.label}
                title={preset.label}
                onClick={() => patchTheme({ preset: preset.id })}
                className={cn(
                  "relative h-10 rounded-lg border",
                  definition.theme.preset === preset.id
                    ? "border-[#1c2127] ring-2 ring-[#1c2127]/15"
                    : "border-[#d5dce1]",
                )}
                style={{ background: `linear-gradient(135deg, ${preset.ink} 50%, ${preset.color} 50%)` }}
              >
                {definition.theme.preset === preset.id ? (
                  <Check className="absolute right-1 bottom-1 size-3 text-white" />
                ) : null}
              </button>
            ))}
          </div>
        </Control>
        <ChoiceGroup
          label="탐색 방식"
          value={definition.shell.navigation}
          choices={[
            { id: "sidebar", label: "사이드바", icon: PanelLeft },
            { id: "topbar", label: "상단바", icon: LayoutPanelTop },
          ]}
          onChange={(navigation) => patchShell({ navigation })}
        />
        <ChoiceGroup
          label="정보 밀도"
          value={definition.shell.density}
          choices={[
            { id: "comfortable", label: "편안하게" },
            { id: "compact", label: "촘촘하게" },
          ]}
          onChange={(density) => patchShell({ density })}
        />
        <ChoiceGroup
          label="콘텐츠 폭"
          value={definition.shell.pageWidth}
          choices={[
            { id: "wide", label: "넓게" },
            { id: "contained", label: "집중형" },
          ]}
          onChange={(pageWidth) => patchShell({ pageWidth })}
        />
        <label className="flex items-start gap-2 rounded-lg border border-[#d5dce1] p-3 text-[12px]">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={definition.shell.showContextBar}
            onChange={(event) =>
              patchShell({ showContextBar: event.target.checked })
            }
          />
          <span>
            <strong className="block text-[#1c2127]">업무 상태 표시</strong>
            <span className="mt-0.5 block text-[11px] leading-4 text-[#738091]">
              선택·필터·동기화 상태를 화면 상단에 계속 보여줍니다.
            </span>
          </span>
        </label>
      </div>
    </aside>
  );
}

function Control({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-[11px] font-semibold text-[#455266]">{label}</span>
      {children}
    </label>
  );
}

function ChoiceGroup<T extends AppNavigation | AppDensity | AppPageWidth>({
  label,
  value,
  choices,
  onChange,
}: {
  label: string;
  value: T;
  choices: ReadonlyArray<{ id: T; label: string; icon?: typeof PanelLeft }>;
  onChange: (value: T) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] font-semibold text-[#455266]">{label}</div>
      <div className="grid grid-cols-2 gap-1.5">
        {choices.map((choice) => {
          const Icon = choice.icon;
          return (
            <button
              key={choice.id}
              type="button"
              onClick={() => onChange(choice.id)}
              className={cn(
                "flex h-9 items-center justify-center gap-1.5 rounded-lg border text-[11px] font-medium",
                value === choice.id
                  ? "border-[#2d72d2] bg-[#e8f0fb] text-[#215db0]"
                  : "border-[#d5dce1] text-[#455266] hover:bg-[#f6f8fa]",
              )}
            >
              {Icon ? <Icon className="size-3.5" /> : null}
              {choice.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
