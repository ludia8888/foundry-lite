import type { MediaProcessorDescriptor } from "@foundry-lite/sdk";
import {
  Braces,
  ChevronDown,
  FlaskConical,
  GitBranch,
  GitCompareArrows,
  LoaderCircle,
  LockKeyhole,
  Play,
  Rocket,
  ScanText,
  Sparkles,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import {
  effectiveSystemPrompt,
  effectiveUserPrompt,
  isDefaultDocumentSchema,
  outputSchemaForStrategy,
  processorForMode,
  promptsForStrategy,
  promptEditability,
  type DocumentExtractionMode,
  type DocumentLabConfig,
  type DocumentLabStrategy,
  type DocumentThinkingMode,
} from "./document-lab-model";
import type { DocumentExtractPromotionTarget } from "./document-lab-comparison-model";

interface DocumentConfigPanelProps {
  config: DocumentLabConfig;
  processors: readonly MediaProcessorDescriptor[];
  isLoadingProcessors: boolean;
  isRunning: boolean;
  isComparing: boolean;
  isPromoting: boolean;
  canRun: boolean;
  canCompare: boolean;
  canPromote: boolean;
  comparisonCount: number;
  promotionTargets: readonly DocumentExtractPromotionTarget[];
  selectedPromotionTargetId: string | null;
  selectedProfileLabel: string | null;
  selectedProfileRuntimeNote: string | null;
  promotionMessage: string | null;
  onChange: (next: DocumentLabConfig) => void;
  onRun: () => void;
  onCompare: () => void;
  onPromotionTargetChange: (targetId: string) => void;
  onPromote: () => void;
}

const STRATEGIES: Array<{
  value: DocumentLabStrategy;
  label: string;
  detail: string;
}> = [
  {
    value: "traditional",
    label: "Traditional",
    detail: "Raw/OCR/layout extraction only",
  },
  {
    value: "structured_prompt",
    label: "Structured prompt",
    detail: "Extracted JSON/text → Use LLM",
  },
  {
    value: "basic_vision",
    label: "Basic Vision",
    detail: "Fixed system · editable user prompt",
  },
  {
    value: "layout_aware_vision",
    label: "Layout-aware VLM",
    detail: "Editable system · fixed position prompt",
  },
];

const EXTRACTION_MODES: Array<{
  value: DocumentExtractionMode;
  label: string;
  detail: string;
}> = [
  { value: "raw", label: "Raw", detail: "Embedded PDF text" },
  { value: "ocr", label: "OCR", detail: "Raster/image text recognition" },
  { value: "layout", label: "Layout-aware", detail: "role + bbox + reading order" },
];

export function DocumentConfigPanel({
  config,
  processors,
  isLoadingProcessors,
  isRunning,
  isComparing,
  isPromoting,
  canRun,
  canCompare,
  canPromote,
  comparisonCount,
  promotionTargets,
  selectedPromotionTargetId,
  selectedProfileLabel,
  selectedProfileRuntimeNote,
  promotionMessage,
  onChange,
  onRun,
  onCompare,
  onPromotionTargetChange,
  onPromote,
}: DocumentConfigPanelProps) {
  const editability = promptEditability(config.strategy);
  const availableProcessorIds = new Set(
    processors.map((processor) => processor.processorId),
  );
  const selectedProcessor = processors.find(
    (processor) => processor.processorId === config.processorId,
  );
  const isVision =
    config.strategy === "basic_vision" ||
    config.strategy === "layout_aware_vision";

  const update = <Key extends keyof DocumentLabConfig>(
    key: Key,
    value: DocumentLabConfig[Key],
  ) => onChange({ ...config, [key]: value });

  const selectExtractionMode = (mode: DocumentExtractionMode) => {
    const processorId = processorForMode(mode, availableProcessorIds);
    onChange({
      ...config,
      extractionMode: mode,
      processorId: processorId ?? "",
    });
  };
  const selectStrategy = (strategy: DocumentLabStrategy) => {
    const prompts = promptsForStrategy(strategy, config);
    onChange({
      ...config,
      strategy,
      ...prompts,
      outputSchemaText: isDefaultDocumentSchema(config.outputSchemaText)
        ? outputSchemaForStrategy(strategy)
        : config.outputSchemaText,
    });
  };

  return (
    <aside className="flex w-[390px] shrink-0 flex-col border-l border-[#C8CED6] bg-white">
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-[#D3D8DE] px-3">
        <div className="flex items-center gap-2">
          <FlaskConical className="size-4 text-[#137CBD]" />
          <div>
            <h2 className="text-[12px] font-semibold">Configuration</h2>
            <p className="text-[9px] text-[#738091]">unsaved experiment profile</p>
          </div>
        </div>
        <span className="rounded bg-[#FFF3D6] px-1.5 py-0.5 text-[9px] font-medium text-[#8A5700]">
          no commit
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <ConfigSection
          title="Input"
          icon={<ScanText className="size-3.5" />}
        >
          <LabeledField label="Media Set ref">
            <Input
              value={config.mediaSetRef}
              className="h-7 rounded-[2px] text-[11px]"
              placeholder="legal.contracts"
              onChange={(event) => update("mediaSetRef", event.target.value)}
            />
          </LabeledField>
          <LabeledField label="Media item version ID" required>
            <Input
              value={config.mediaItemVersionId}
              className="h-7 rounded-[2px] font-mono text-[10px]"
              placeholder="mver_..."
              onChange={(event) =>
                update("mediaItemVersionId", event.target.value)
              }
            />
          </LabeledField>
          <LabeledField label="Security classification">
            <Select
              value={config.dataClassification}
              onValueChange={(value) => update("dataClassification", value)}
            >
              <SelectTrigger className="h-7 rounded-[2px] text-[11px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["public", "internal", "confidential", "restricted"].map(
                  (classification) => (
                    <SelectItem
                      key={classification}
                      value={classification}
                      className="text-[11px]"
                    >
                      {classification}
                    </SelectItem>
                  ),
                )}
              </SelectContent>
            </Select>
          </LabeledField>
        </ConfigSection>

        <ConfigSection
          title="Extraction strategy"
          icon={<Sparkles className="size-3.5" />}
        >
          <div className="grid grid-cols-2 gap-1.5">
            {STRATEGIES.map((strategy) => (
              <button
                key={strategy.value}
                type="button"
                className={cn(
                  "rounded-[2px] border p-2 text-left",
                  config.strategy === strategy.value
                    ? "border-[#137CBD] bg-[#E1F3F8]"
                    : "border-[#D3D8DE] hover:bg-[#F6F7F9]",
                )}
                onClick={() => selectStrategy(strategy.value)}
              >
                <span className="block text-[10px] font-semibold">
                  {strategy.label}
                </span>
                <span className="mt-0.5 block text-[9px] leading-3 text-[#738091]">
                  {strategy.detail}
                </span>
              </button>
            ))}
          </div>
          <Button
            type="button"
            variant="outline"
            className="h-8 w-full rounded-[2px] border-[#7C91A7] text-[10px]"
            disabled={!canCompare || isComparing || isRunning}
            onClick={onCompare}
          >
            {isComparing ? (
              <LoaderCircle className="mr-1.5 size-3.5 animate-spin" />
            ) : (
              <GitCompareArrows className="mr-1.5 size-3.5" />
            )}
            {isComparing
              ? "Comparing the same PDF..."
              : "Compare Raw · OCR · Layout · VLM"}
          </Button>
          <p className="text-[9px] leading-3.5 text-[#738091]">
            각 전략은 별도 no-commit preview run으로 실행됩니다. 지원되지 않는
            PDF processor는 실패를 숨기지 않고 이유를 표시합니다.
          </p>

          {!isVision ? (
            <>
              <div className="grid grid-cols-3 gap-1">
                {EXTRACTION_MODES.map((mode) => {
                  const processor = processorForMode(
                    mode.value,
                    availableProcessorIds,
                  );
                  return (
                    <button
                      key={mode.value}
                      type="button"
                      disabled={!processor && !isLoadingProcessors}
                      title={
                        processor
                          ? mode.detail
                          : "현재 runtime에 이 전략의 processor가 없습니다."
                      }
                      className={cn(
                        "rounded-[2px] border px-1.5 py-1.5 text-left disabled:cursor-not-allowed disabled:opacity-45",
                        config.extractionMode === mode.value
                          ? "border-[#137CBD] bg-[#E1F3F8]"
                          : "border-[#D3D8DE] hover:bg-[#F6F7F9]",
                      )}
                      onClick={() => selectExtractionMode(mode.value)}
                    >
                      <span className="block text-[10px] font-medium">
                        {mode.label}
                      </span>
                    </button>
                  );
                })}
              </div>
              <LabeledField label="Pinned processor">
                <Select
                  value={config.processorId}
                  onValueChange={(value) => update("processorId", value)}
                >
                  <SelectTrigger className="h-7 rounded-[2px] font-mono text-[10px]">
                    <SelectValue
                      placeholder={
                        isLoadingProcessors
                          ? "processor loading..."
                          : "processor unavailable"
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {processors.map((processor) => (
                      <SelectItem
                        key={processor.processorId}
                        value={processor.processorId}
                        className="font-mono text-[10px]"
                      >
                        {processor.processorId}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selectedProcessor ? (
                  <p className="mt-1 text-[9px] text-[#738091]">
                    formats={selectedProcessor.inputFormats.join(", ")} · output=
                    {selectedProcessor.outputKinds.join(", ")}
                  </p>
                ) : null}
              </LabeledField>
              <div className="grid grid-cols-2 gap-2">
                <LabeledField label="Start page">
                  <Input
                    type="number"
                    min={1}
                    value={config.pageStart}
                    className="h-7 rounded-[2px] text-[11px]"
                    onChange={(event) =>
                      update("pageStart", Math.max(1, Number(event.target.value)))
                    }
                  />
                </LabeledField>
                <LabeledField label="Preview pages">
                  <Input
                    type="number"
                    min={1}
                    max={10}
                    value={config.pageLimit}
                    className="h-7 rounded-[2px] text-[11px]"
                    onChange={(event) =>
                      update(
                        "pageLimit",
                        Math.min(10, Math.max(1, Number(event.target.value))),
                      )
                    }
                  />
                </LabeledField>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <LabeledField label="Chunk size">
                  <Input
                    type="number"
                    min={1}
                    value={config.chunkSize}
                    className="h-7 rounded-[2px] text-[11px]"
                    onChange={(event) =>
                      update("chunkSize", Number(event.target.value))
                    }
                  />
                </LabeledField>
                <LabeledField label="Overlap">
                  <Input
                    type="number"
                    min={0}
                    value={config.overlap}
                    className="h-7 rounded-[2px] text-[11px]"
                    onChange={(event) =>
                      update("overlap", Number(event.target.value))
                    }
                  />
                </LabeledField>
              </div>
            </>
          ) : null}
        </ConfigSection>

        {config.strategy !== "traditional" ? (
          <ConfigSection
            title="Prompt & typed output"
            icon={<Braces className="size-3.5" />}
          >
            <div className="grid grid-cols-2 gap-2">
              <LabeledField label="Model alias">
                <Input
                  value={config.modelAlias}
                  className="h-7 rounded-[2px] font-mono text-[10px]"
                  onChange={(event) =>
                    update("modelAlias", event.target.value)
                  }
                />
              </LabeledField>
              <LabeledField label="Prompt version">
                <Input
                  value={config.promptVersionId}
                  className="h-7 rounded-[2px] font-mono text-[10px]"
                  onChange={(event) =>
                    update("promptVersionId", event.target.value)
                  }
                />
              </LabeledField>
            </div>
            <PromptField
              label="System prompt"
              value={effectiveSystemPrompt(config)}
              isEditable={editability.canEditSystemPrompt}
              onChange={(value) => update("systemPrompt", value)}
            />
            <PromptField
              label="User prompt"
              value={effectiveUserPrompt(config)}
              isEditable={editability.canEditUserPrompt}
              onChange={(value) => update("userPrompt", value)}
            />
            <LabeledField label="Output JSON Schema">
              <Textarea
                value={config.outputSchemaText}
                className="min-h-36 resize-y rounded-[2px] font-mono text-[9px] leading-3.5"
                onChange={(event) =>
                  update("outputSchemaText", event.target.value)
                }
              />
            </LabeledField>
            <div className="grid grid-cols-2 gap-2">
              <LabeledField label="Temperature">
                <Input
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  value={config.temperature}
                  className="h-7 rounded-[2px] text-[11px]"
                  onChange={(event) =>
                    update("temperature", Number(event.target.value))
                  }
                />
              </LabeledField>
              <LabeledField label="Max output tokens">
                <Input
                  type="number"
                  min={1}
                  value={config.maxOutputTokens}
                  className="h-7 rounded-[2px] text-[11px]"
                  onChange={(event) =>
                    update("maxOutputTokens", Number(event.target.value))
                  }
                />
              </LabeledField>
            </div>
            <LabeledField label="Thinking mode">
              <Select
                value={config.thinkingMode}
                onValueChange={(value) =>
                  update("thinkingMode", value as DocumentThinkingMode)
                }
              >
                <SelectTrigger
                  aria-label="Document model thinking mode"
                  className="h-7 rounded-[2px] text-[11px]"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="disabled">
                    Disabled · deterministic extraction
                  </SelectItem>
                  <SelectItem value="adaptive">
                    Adaptive · complex interpretation
                  </SelectItem>
                  <SelectItem value="provider_default">
                    Provider default
                  </SelectItem>
                </SelectContent>
              </Select>
            </LabeledField>
            <LabeledField label="Trial rows · max 50">
              <Input
                type="number"
                min={1}
                max={50}
                value={config.trialCount}
                className="h-7 rounded-[2px] text-[11px]"
                onChange={(event) =>
                  update("trialCount", Number(event.target.value))
                }
              />
            </LabeledField>
            <label className="flex items-center gap-2 text-[10px]">
              <Checkbox
                checked={config.includeErrors}
                onCheckedChange={(checked) =>
                  update("includeErrors", checked === true)
                }
              />
              Include row-level parse/model errors in output
            </label>
          </ConfigSection>
        ) : null}

        <ConfigSection
          title="Deployment profile"
          icon={<Rocket className="size-3.5" />}
        >
          <div className="rounded-[2px] border border-[#D3D8DE] bg-[#F6F7F9] p-2">
            <div className="flex items-center gap-1.5 text-[10px] font-medium">
              <LockKeyhole className="size-3 text-[#738091]" />
              Immutable extraction profile
            </div>
            <p className="mt-1 text-[9px] leading-3.5 text-[#738091]">
              Preview가 검증되면 processor/chunk와, semantic 전략에서는
              Use LLM model revision/prompt/schema까지 실행 가능한 graph fragment로
              승격합니다.
            </p>
            <div className="mt-2">
              <LabeledField label="Target draft document.extract">
                <Select
                  value={selectedPromotionTargetId ?? ""}
                  disabled={promotionTargets.length === 0}
                  onValueChange={onPromotionTargetChange}
                >
                  <SelectTrigger
                    aria-label="Promotion target"
                    className="h-7 rounded-[2px] text-[10px]"
                  >
                    <SelectValue
                      placeholder={
                        promotionTargets.length > 0
                          ? "Choose a draft node"
                          : "No Graph v2 document.extract node"
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {promotionTargets.map((target) => (
                      <SelectItem
                        key={target.id}
                        value={target.id}
                        className="text-[10px]"
                      >
                        <span className="flex items-center gap-1.5">
                          <GitBranch className="size-3" />
                          {target.label}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </LabeledField>
            </div>
            {selectedProfileLabel ? (
              <p className="mt-2 break-all font-mono text-[8px] text-[#5F6B7C]">
                selected={selectedProfileLabel}
              </p>
            ) : null}
            {selectedProfileRuntimeNote ? (
              <p className="mt-2 rounded-[2px] border border-[#E2C36E] bg-[#FFF8E5] px-2 py-1.5 text-[9px] leading-3.5 text-[#775500]">
                {selectedProfileRuntimeNote}
              </p>
            ) : null}
            <Button
              type="button"
              variant="outline"
              className="mt-2 h-7 w-full rounded-[2px] text-[10px]"
              disabled={!canPromote || isPromoting}
              title={
                canPromote
                  ? "성공한 no-commit run의 exact extraction/chunk/semantic profile을 선택한 draft graph에 기록합니다."
                  : "성공한 preview result와 Graph v2 document.extract target이 필요합니다."
              }
              onClick={onPromote}
            >
              {isPromoting ? (
                <LoaderCircle className="mr-1.5 size-3 animate-spin" />
              ) : (
                <Rocket className="mr-1.5 size-3" />
              )}
              {isPromoting ? "Promoting exact profile..." : "Promote exact profile"}
            </Button>
            {promotionMessage ? (
              <p
                className="mt-2 rounded-[2px] border border-[#A9D6B8] bg-[#E7F6EC] px-2 py-1.5 text-[9px] leading-3.5 text-[#0F6B3E]"
                role="status"
              >
                {promotionMessage}
              </p>
            ) : null}
            {promotionTargets.length === 0 ? (
              <p className="mt-2 text-[9px] leading-3.5 text-[#8A5700]">
                Pipeline Builder draft에 document.extract 노드를 먼저 추가해야
                합니다.
              </p>
            ) : null}
            {comparisonCount > 0 ? (
              <p className="mt-2 text-[8px] text-[#738091]">
                comparison evidence {comparisonCount}개 · 선택한 카드의 run만
                승격됩니다.
              </p>
            ) : null}
          </div>
        </ConfigSection>
      </div>

      <div className="shrink-0 border-t border-[#D3D8DE] bg-[#F6F7F9] p-3">
        <Button
          className="h-8 w-full rounded-[2px] text-[11px]"
          disabled={!canRun || isRunning}
          onClick={onRun}
        >
          <Play className="mr-1.5 size-3.5" />
          {isRunning ? "Preview running..." : "Run no-commit preview"}
        </Button>
        <p className="mt-1.5 text-center text-[9px] text-[#738091]">
          출력 Dataset/Media version은 생성되지 않습니다.
        </p>
      </div>
    </aside>
  );
}

function ConfigSection({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-[#E2E6EA] p-3">
      <h3 className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold tracking-[0.5px] text-[#5F6B7C] uppercase">
        {icon}
        {title}
        <ChevronDown className="ml-auto size-3" />
      </h3>
      <div className="space-y-2.5">{children}</div>
    </section>
  );
}

function LabeledField({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-1 text-[9px] font-medium text-[#5F6B7C]">
        {label}
        {required ? <span className="text-[#C23030]">*</span> : null}
      </span>
      {children}
    </label>
  );
}

function PromptField({
  label,
  value,
  isEditable,
  onChange,
}: {
  label: string;
  value: string;
  isEditable: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <LabeledField label={label}>
      <div className="relative">
        <Textarea
          value={value}
          readOnly={!isEditable}
          className={cn(
            "min-h-20 resize-y rounded-[2px] pr-20 text-[10px] leading-4",
            !isEditable && "bg-[#F1F3F5] text-[#5F6B7C]",
          )}
          onChange={(event) => onChange(event.target.value)}
        />
        <span
          className={cn(
            "absolute top-1.5 right-1.5 rounded px-1 py-0.5 text-[8px] font-medium",
            isEditable
              ? "bg-[#E7F6EC] text-[#0F6B3E]"
              : "bg-[#EDEFF2] text-[#5F6B7C]",
          )}
        >
          {isEditable ? "editable" : "fixed"}
        </span>
      </div>
    </LabeledField>
  );
}
