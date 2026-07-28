import type {
  PipelineGraphV2,
  TrainedModelDescriptor,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import {
  ArrowLeft,
  ArrowRight,
  Braces,
  Cpu,
  ExternalLink,
  FileText,
  LockKeyhole,
  RefreshCcw,
  Sparkles,
  Table2,
} from "lucide-react";
import { useMemo, useState } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  asText,
  importedTrainedModelRefs,
  nodeDataOf,
  nodeLabel,
  type PipelineCanvasNode,
} from "../pipeline-model";
import { useSafeQuery } from "../use-safe-query";
import {
  useLlmTrialCount,
  withUseLlmDraftConfiguration,
} from "../pipeline-use-llm-trial-model";
import {
  MediaTransformBoard,
  VisionEmbeddingBoard,
} from "./PipelineMediaConfigurationBoard";
import { UseLlmTrialPanel } from "./UseLlmTrialPanel";

interface PipelineNodeConfigurationBoardProps {
  node: PipelineCanvasNode;
  branchId: string | null;
  graph: PipelineGraphV2 | null;
  isGraphDirty: boolean;
  onApply: (nodeId: string, patch: Record<string, unknown>) => void;
  onClose: () => void;
}

type BoardTab = "configuration" | "inputs" | "output";
type SemanticPromptMode =
  | "text"
  | "basic_vision"
  | "layout_aware_vision";
const MAX_CACHE_GENERATION = 2_147_483_647;

export function PipelineNodeConfigurationBoard({
  node,
  branchId,
  graph,
  isGraphDirty,
  onApply,
  onClose,
}: PipelineNodeConfigurationBoardProps) {
  const [activeTab, setActiveTab] = useState<BoardTab>("configuration");

  return (
    <section
      aria-label={`${nodeLabel(node)} configuration board`}
      className="flex min-h-0 flex-1 flex-col bg-[#F4F6F8]"
    >
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-[#C5CBD3] bg-white px-3">
        <Button
          variant="ghost"
          size="sm"
          className="h-7 rounded-[2px] px-2 text-[11px]"
          onClick={onClose}
        >
          <ArrowLeft className="size-3.5" />
          그래프로 돌아가기
        </Button>
        <span className="h-5 w-px bg-[#D5DAE0]" />
        <div className="min-w-0">
          <div className="truncate text-[13px] font-semibold">
            {nodeLabel(node)}
          </div>
          <div className="font-mono text-[9px] text-muted-foreground">
            {node.descriptorId}@{node.specVersion}
          </div>
        </div>
        <StatusPill intent="info" className="ml-auto">
          Graph v2 · named ports
        </StatusPill>
        {node.descriptor?.availability === "validation_only" ? (
          <StatusPill intent="warning">preview / validation only</StatusPill>
        ) : (
          <StatusPill intent="success">executable</StatusPill>
        )}
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[176px_minmax(0,1fr)]">
        <BoardNavigation
          activeTab={activeTab}
          onChange={setActiveTab}
          node={node}
        />
        {node.descriptorId === "transform.document_extract" ? (
          <DocumentExtractBoard
            node={node}
            activeTab={activeTab}
            onApply={onApply}
          />
        ) : node.descriptorId === "transform.media" ? (
          <MediaTransformBoard
            node={node}
            activeTab={activeTab}
            onApply={onApply}
          />
        ) : node.descriptorId === "transform.embedding.vision" ? (
          <VisionEmbeddingBoard
            node={node}
            activeTab={activeTab}
            onApply={onApply}
          />
        ) : node.descriptorId === "transform.trained_model" ? (
          <TrainedModelBoard
            node={node}
            graph={graph}
            activeTab={activeTab}
            onApply={onApply}
          />
        ) : ["source.stream", "source.geospatial", "output.geospatial"].includes(
            node.descriptorId,
          ) ? (
          <StructuredDataBoard node={node} activeTab={activeTab} onApply={onApply} />
        ) : (
          <UseLlmBoard
            node={node}
            branchId={branchId}
            graph={graph}
            isGraphDirty={isGraphDirty}
            activeTab={activeTab}
            onApply={onApply}
          />
        )}
      </div>
    </section>
  );
}

function StructuredDataBoard({
  node,
  activeTab,
  onApply,
}: {
  node: PipelineCanvasNode;
  activeTab: BoardTab;
  onApply: (nodeId: string, patch: Record<string, unknown>) => void;
}) {
  const current = nodeDataOf(node);
  const [resourceRef, setResourceRef] = useState(
    asText(current.sourceRef ?? current.resourceRef) ?? "",
  );
  const [geometryField, setGeometryField] = useState(
    asText(current.geometryField) ?? "geometry",
  );
  const [longitudeField, setLongitudeField] = useState(
    asText(current.longitudeField) ?? "",
  );
  const [latitudeField, setLatitudeField] = useState(
    asText(current.latitudeField) ?? "",
  );
  const [timeField, setTimeField] = useState(asText(current.timeField) ?? "");
  const isStream = node.descriptorId === "source.stream";
  const patch = isStream
    ? { sourceRef: resourceRef.trim() }
    : {
        resourceRef: resourceRef.trim(),
        geometryField: geometryField.trim(),
        longitudeField: longitudeField.trim(),
        latitudeField: latitudeField.trim(),
        timeField: timeField.trim(),
      };

  if (activeTab !== "configuration") {
    return (
      <div className="overflow-auto p-5 text-[11px]">
        <div className="rounded-[2px] border border-[#C5CBD3] bg-white p-4">
          <div className="font-semibold">
            {activeTab === "inputs" ? "Pinned input contract" : "Output artifact contract"}
          </div>
          <p className="mt-2 leading-5 text-muted-foreground">
            {isStream
              ? "선택한 managed sync의 마지막 성공 run, 정확한 Dataset version, partition checkpoint를 함께 고정합니다. Pipeline build는 broker를 다시 읽지 않습니다."
              : "EPSG:4326 공간 필드와 선택적 time axis를 검증하고 geospatial_series Artifact Passport로 기록합니다."}
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="overflow-auto p-5">
      <div className="max-w-2xl space-y-4 rounded-[2px] border border-[#C5CBD3] bg-white p-4">
        <Field label={isStream ? "Managed streaming sync name" : "Dataset resource reference"}>
          <Input
            aria-label={isStream ? "Managed streaming sync name" : "Dataset resource reference"}
            value={resourceRef}
            onChange={(event) => setResourceRef(event.target.value)}
          />
        </Field>
        {!isStream ? (
          <div className="grid grid-cols-2 gap-3">
            <Field label="GeoJSON geometry field">
              <Input
                aria-label="GeoJSON geometry field"
                value={geometryField}
                onChange={(event) => setGeometryField(event.target.value)}
              />
            </Field>
            <Field label="Event time field (optional)">
              <Input
                aria-label="Event time field"
                value={timeField}
                onChange={(event) => setTimeField(event.target.value)}
              />
            </Field>
            <Field label="Longitude field (geometry 대안)">
              <Input
                aria-label="Longitude field"
                value={longitudeField}
                onChange={(event) => setLongitudeField(event.target.value)}
              />
            </Field>
            <Field label="Latitude field (geometry 대안)">
              <Input
                aria-label="Latitude field"
                value={latitudeField}
                onChange={(event) => setLatitudeField(event.target.value)}
              />
            </Field>
          </div>
        ) : null}
        <div className="rounded-[2px] border border-[#D5DAE0] bg-[#F7F8FA] p-3 text-[10px] leading-5 text-muted-foreground">
          {isStream
            ? "지원 경로: Kafka · CDC · WebSocket managed sync. 별도 stream engine은 추가하지 않습니다."
            : "GeoJSON geometry 또는 longitude/latitude 중 하나를 실제 committed schema와 대조합니다."}
        </div>
        <Button
          className="rounded-[2px]"
          disabled={!resourceRef.trim()}
          onClick={() => onApply(node.id, patch)}
        >
          Configuration 적용
        </Button>
      </div>
    </div>
  );
}

function BoardNavigation({
  activeTab,
  onChange,
  node,
}: {
  activeTab: BoardTab;
  onChange: (tab: BoardTab) => void;
  node: PipelineCanvasNode;
}) {
  const tabs: Array<{ id: BoardTab; label: string; icon: typeof Braces }> = [
    { id: "configuration", label: "Configuration", icon: Braces },
    { id: "inputs", label: "Inputs", icon: Table2 },
    { id: "output", label: "Output contract", icon: FileText },
  ];
  return (
    <nav
      aria-label="Configuration board sections"
      className="border-r border-[#C5CBD3] bg-[#F7F8FA] p-2"
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          className={cn(
            "mb-1 flex w-full items-center gap-2 rounded-[2px] px-2 py-2 text-left text-[11px]",
            activeTab === tab.id
              ? "bg-[#DCEAF7] font-semibold text-[#145A8D]"
              : "hover:bg-[#EDEFF2]",
          )}
          onClick={() => onChange(tab.id)}
        >
          <tab.icon className="size-3.5" />
          {tab.label}
        </button>
      ))}
      <div className="mt-4 border-t border-[#D5DAE0] pt-3">
        <div className="text-[9px] font-semibold tracking-[0.08em] text-muted-foreground uppercase">
          Port contract
        </div>
        <div className="mt-2 space-y-1 font-mono text-[9px]">
          <div className="border border-[#C5CBD3] bg-white px-2 py-1.5">
            in · {inputPortLabel(node)}
          </div>
          <div className="border border-[#C5CBD3] bg-white px-2 py-1.5">
            out · {outputPortLabel(node)}
          </div>
        </div>
      </div>
    </nav>
  );
}

function DocumentExtractBoard({
  node,
  activeTab,
  onApply,
}: {
  node: PipelineCanvasNode;
  activeTab: BoardTab;
  onApply: (nodeId: string, patch: Record<string, unknown>) => void;
}) {
  const config = nodeDataOf(node);
  const parameters = recordValue(config.parameters);
  const pageSelection = recordValue(parameters?.pageSelection);
  const [label, setLabel] = useState(asText(config.label) ?? "Document extract");
  const [processorId, setProcessorId] = useState(
    asText(config.processorId) ?? "pdf_text_v1@1",
  );
  const [profileName, setProfileName] = useState(
    asText(config.profileName) ?? "document-default@1",
  );
  const [strategy, setStrategy] = useState(
    asText(config.extractionStrategy) ?? "raw",
  );
  const [outputFormat, setOutputFormat] = useState(
    asText(config.outputFormat) ?? "markdown",
  );
  const [previewStartPage, setPreviewStartPage] = useState(
    String(pageSelection?.start ?? "1"),
  );
  const [previewPageLimit, setPreviewPageLimit] = useState(
    String(pageSelection?.limit ?? "3"),
  );
  const [userPrompt, setUserPrompt] = useState(
    asText(config.promptTemplate) ??
      asText(config.userPrompt) ??
      (strategy === "layout_aware_vision" ? DEFAULT_LAYOUT_USER_PROMPT : ""),
  );
  const [systemPrompt, setSystemPrompt] = useState(
    asText(config.systemPrompt) ??
      (strategy === "basic_vision" ? DEFAULT_VISION_SYSTEM_PROMPT : ""),
  );
  const promptMode =
    strategy === "basic_vision"
      ? "basic_vision"
      : strategy === "layout_aware_vision"
        ? "layout_aware_vision"
        : "none";

  const handleApply = () => {
    const nextParameters = { ...(parameters ?? {}) };
    delete nextParameters.maxPages;
    nextParameters.pageSelection = {
      start: Number(previewStartPage),
      limit: Number(previewPageLimit),
    };
    onApply(node.id, {
      label,
      processorId,
      profileName,
      extractionStrategy: strategy,
      outputFormat,
      promptMode,
      userPrompt: undefined,
      promptTemplate: isPromptMode(promptMode) ? userPrompt : undefined,
      systemPrompt: isPromptMode(promptMode)
        ? systemPrompt.trim() || undefined
        : undefined,
      parameters: nextParameters,
    });
  };

  if (activeTab === "inputs") {
    return (
      <BoardReadingPane
        title="Media input"
        description="Media Set 또는 Media derivative artifact만 입력할 수 있습니다. 원문은 테이블로 미리 평탄화되지 않습니다."
      >
        <ContractFlow
          steps={[
            "media_set_selection",
            "document.extract",
            "content_unit_set",
          ]}
        />
        <EvidenceCallout>
          PDF preview는 최대 3페이지가 기본이며, page·bbox·structure·confidence
          좌표를 Content Unit에 유지해야 합니다.
        </EvidenceCallout>
      </BoardReadingPane>
    );
  }
  if (activeTab === "output") {
    return (
      <BoardReadingPane
        title="Content Unit output"
        description="추출 결과는 Dataset 행이 아니라 page/bbox/structure 좌표를 가진 typed content artifact입니다."
      >
        <pre className="overflow-auto border border-[#C5CBD3] bg-[#17212B] p-3 font-mono text-[10px] leading-5 text-[#D7E0EA]">
          {DOCUMENT_OUTPUT_SAMPLE}
        </pre>
      </BoardReadingPane>
    );
  }

  const isPromptProfile = promptMode !== "none";
  const isPromptContractValid =
    !isPromptMode(promptMode) || Boolean(userPrompt.trim());
  const isPageSelectionValid =
    Number.isInteger(Number(previewStartPage)) &&
    Number(previewStartPage) >= 1 &&
    Number.isInteger(Number(previewPageLimit)) &&
    Number(previewPageLimit) >= 1 &&
    Number(previewPageLimit) <= 10;
  return (
    <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_280px]">
      <div className="min-h-0 overflow-y-auto p-4">
        <div className="mx-auto max-w-3xl space-y-4">
          <BoardTitle
            icon={FileText}
            title="Document extraction profile"
            description="processor, extraction strategy, output format, editable prompt defaults를 하나의 versioned node config로 저장합니다."
          />
          <div className="grid gap-3 border border-[#C5CBD3] bg-white p-3 md:grid-cols-2">
            <Field label="Node name">
              <Input
                aria-label="Document extract node name"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </Field>
            <Field label="Profile version">
              <Input
                aria-label="Document extraction profile"
                className="font-mono"
                value={profileName}
                onChange={(event) => setProfileName(event.target.value)}
              />
            </Field>
            <Field label="Processor pin">
              <Input
                aria-label="Document processor pin"
                className="font-mono"
                value={processorId}
                onChange={(event) => setProcessorId(event.target.value)}
              />
            </Field>
            <Field label="Preview start page">
              <Input
                aria-label="Document preview start page"
                type="number"
                min={1}
                value={previewStartPage}
                onChange={(event) => setPreviewStartPage(event.target.value)}
              />
            </Field>
            <Field label="Preview page count">
              <Input
                aria-label="Document preview page count"
                type="number"
                min={1}
                max={10}
                value={previewPageLimit}
                onChange={(event) => setPreviewPageLimit(event.target.value)}
              />
            </Field>
          </div>

          <section className="border border-[#C5CBD3] bg-white p-3">
            <SectionHeading
              title="Extraction strategy"
              description="공개 Foundry 흐름의 raw·OCR·layout·VLM 실험 단위를 profile로 고정합니다."
            />
            <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-5">
              {DOCUMENT_STRATEGIES.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  aria-pressed={strategy === option.id}
                  className={cn(
                    "min-h-16 border px-2 py-2 text-left text-[10px]",
                    strategy === option.id
                      ? "border-[#2D72D2] bg-[#EAF2FC] text-[#174A7E]"
                      : "border-[#C5CBD3] hover:bg-[#F7F8FA]",
                  )}
                  onClick={() => setStrategy(option.id)}
                >
                  <div className="font-semibold">{option.label}</div>
                  <div className="mt-1 leading-4 text-muted-foreground">
                    {option.detail}
                  </div>
                </button>
              ))}
            </div>
            <div className="mt-3 w-52">
              <Field label="Output format">
                <Select value={outputFormat} onValueChange={setOutputFormat}>
                  <SelectTrigger aria-label="Document output format">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="text">Text</SelectItem>
                    <SelectItem value="markdown">Markdown</SelectItem>
                    <SelectItem value="html">HTML</SelectItem>
                    <SelectItem value="layout_json">Layout JSON</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </div>
          </section>

          {isPromptProfile ? (
            <section className="border border-[#C5CBD3] bg-white p-3">
              <SectionHeading
                title="Prompt contract"
                description="VLM profile은 안전한 기본값을 제공하지만 user prompt와 system prompt를 모두 편집하고 버전으로 저장합니다."
              />
              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                <PromptField
                  label="System prompt"
                  value={systemPrompt}
                  isLocked={false}
                  onChange={setSystemPrompt}
                />
                <PromptField
                  label="User prompt"
                  value={userPrompt}
                  isLocked={false}
                  onChange={setUserPrompt}
                />
              </div>
            </section>
          ) : null}

          <div className="flex items-center justify-end gap-2 border-t border-[#C5CBD3] pt-3">
            <Button
              asChild
              variant="outline"
              className="rounded-[2px]"
            >
              <a href="/document-intelligence">
                Document Intelligence Lab
                <ExternalLink className="size-3.5" />
              </a>
            </Button>
            <Button
              className="rounded-[2px]"
              disabled={
                !label.trim() ||
                !processorId.trim() ||
                !isPageSelectionValid ||
                !isPromptContractValid
              }
              onClick={handleApply}
            >
              Apply configuration
            </Button>
          </div>
        </div>
      </div>
      <DocumentProfileSummary
        strategy={strategy}
        promptMode={promptMode}
        processorId={processorId}
        outputFormat={outputFormat}
      />
    </div>
  );
}

function TrainedModelBoard({
  node,
  graph,
  activeTab,
  onApply,
}: {
  node: PipelineCanvasNode;
  graph: PipelineGraphV2 | null;
  activeTab: BoardTab;
  onApply: (nodeId: string, patch: Record<string, unknown>) => void;
}) {
  const client = useFoundryLiteClient();
  const config = nodeDataOf(node);
  const models = useSafeQuery(
    ["pipelines", "trained-model-board"],
    () => client.pipelines.trainedModels(),
  );
  const importedRefs = useMemo(
    () => new Set(importedTrainedModelRefs(graph)),
    [graph],
  );
  const importedModels = (models.data?.items ?? []).filter((model) =>
    importedRefs.has(model.modelRef),
  );
  const [modelRef, setModelRef] = useState(
    asText(config.modelRef) ?? "demo.transaction-risk",
  );
  const [branch, setBranch] = useState(
    asText(config.modelBranch) ?? "master",
  );
  const [inputMappings, setInputMappings] = useState<Record<string, string>>(
    textRecord(config.inputMappings),
  );
  const [outputMappings, setOutputMappings] = useState<Record<string, string>>(
    textRecord(config.outputMappings),
  );
  const selected = importedModels.find(
    (model) => model.modelRef === modelRef,
  );
  const apply = () =>
    onApply(node.id, {
      modelRef,
      modelBranch: branch,
      fallbackBranches: ["master"],
      inputMappings,
      outputMappings,
    });

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex h-10 shrink-0 items-center border-b border-[#C5CBD3] bg-[#F7F8FA] px-3">
        <div className="flex items-center gap-2 text-[11px] font-semibold">
          <Cpu className="size-3.5 text-[#394B59]" />
          Trained Model API mapping
        </div>
        <StatusPill intent="info" className="ml-auto">Batch only</StatusPill>
        <StatusPill intent="warning" className="ml-1">Preview unavailable</StatusPill>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        {activeTab === "configuration" ? (
          <TrainedModelConfiguration
            models={importedModels}
            modelRef={modelRef}
            branch={branch}
            selected={selected}
            onModelChange={setModelRef}
            onBranchChange={setBranch}
          />
        ) : activeTab === "inputs" ? (
          <TrainedModelMappingGrid
            title="Input API schema"
            fields={selected?.inputSchema ?? []}
            mappings={inputMappings}
            placeholder={(field) => `$${field.name}`}
            onChange={setInputMappings}
          />
        ) : (
          <TrainedModelMappingGrid
            title="Output aliases"
            fields={selected?.outputSchema ?? []}
            mappings={outputMappings}
            placeholder={(field) => field.name}
            onChange={setOutputMappings}
          />
        )}
      </div>
      <div className="flex h-12 shrink-0 items-center border-t border-[#C5CBD3] bg-white px-3">
        <p className="text-[9px] text-muted-foreground">
          build 시 branch 최신 버전을 해석하고 execution plan과 artifact manifest에 고정합니다.
        </p>
        <Button className="ml-auto h-7 rounded-[2px] text-[10px]" onClick={apply}>
          Apply mapping
        </Button>
      </div>
    </div>
  );
}

function TrainedModelConfiguration({
  models,
  modelRef,
  branch,
  selected,
  onModelChange,
  onBranchChange,
}: {
  models: TrainedModelDescriptor[];
  modelRef: string;
  branch: string;
  selected: TrainedModelDescriptor | undefined;
  onModelChange: (value: string) => void;
  onBranchChange: (value: string) => void;
}) {
  return (
    <div className="mx-auto max-w-[900px] space-y-4">
      <section className="border border-[#C5CBD3] bg-white">
        <div className="border-b border-[#C5CBD3] bg-[#F7F8FA] px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.08em]">
          Reusable model
        </div>
        <div className="grid grid-cols-2 gap-4 p-3">
          <div>
            <Label className="text-[10px]">Imported model</Label>
            <Select value={modelRef} onValueChange={onModelChange}>
              <SelectTrigger className="mt-1 h-8 rounded-[2px] text-[11px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {models.map((model) => (
                  <SelectItem key={model.modelRef} value={model.modelRef}>
                    {model.displayName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-[10px]">Model branch</Label>
            <Input
              className="mt-1 h-8 rounded-[2px] font-mono text-[11px]"
              value={branch}
              onChange={(event) => onBranchChange(event.target.value)}
            />
          </div>
        </div>
      </section>
      {selected ? <TrainedModelPassport model={selected} /> : null}
    </div>
  );
}

function TrainedModelPassport({ model }: { model: TrainedModelDescriptor }) {
  return (
    <section className="border border-[#C5CBD3] bg-white p-3">
      <div className="grid grid-cols-4 gap-px border border-[#C5CBD3] bg-[#C5CBD3] text-[10px]">
        <PassportCell label="version resolution" value={`${model.branch} → ${model.latestVersion}`} />
        <PassportCell label="CPU" value={`${model.resourceProfile.cpuCores} core`} />
        <PassportCell label="memory" value={`${model.resourceProfile.memoryMiB} MiB`} />
        <PassportCell label="GPU" value={model.resourceProfile.gpuType} />
      </div>
      <p className="mt-3 border-l-2 border-[#D9822B] bg-[#FFF4E8] px-2 py-1.5 text-[10px] leading-4 text-[#7A4314]">
        공개 계약상 preview와 streaming은 지원하지 않습니다. single tabular input과 single tabular output만 허용합니다.
      </p>
    </section>
  );
}

function PassportCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[#F7F8FA] p-2">
      <div className="text-[8px] uppercase tracking-[0.08em] text-muted-foreground">{label}</div>
      <div className="mt-1 font-mono text-[10px]">{value}</div>
    </div>
  );
}

function TrainedModelMappingGrid({
  title,
  fields,
  mappings,
  placeholder,
  onChange,
}: {
  title: string;
  fields: Array<{ name: string; type: string; required: boolean }>;
  mappings: Record<string, string>;
  placeholder: (field: { name: string; type: string }) => string;
  onChange: (value: Record<string, string>) => void;
}) {
  return (
    <section className="mx-auto max-w-[980px] border border-[#C5CBD3] bg-white">
      <div className="grid grid-cols-[220px_minmax(0,1fr)_140px] border-b border-[#C5CBD3] bg-[#F7F8FA] px-3 py-2 text-[9px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
        <span>{title}</span><span>Pipeline expression / alias</span><span>API type</span>
      </div>
      {fields.map((field) => (
        <div key={field.name} className="grid grid-cols-[220px_minmax(0,1fr)_140px] items-center gap-3 border-b border-[#E1E5EA] px-3 py-2 last:border-b-0">
          <div className="font-mono text-[10px]">
            {field.name}{field.required ? <span className="ml-1 text-[#C23030]">*</span> : null}
          </div>
          <Input
            aria-label={`${field.name} mapping`}
            className="h-8 rounded-[2px] font-mono text-[11px]"
            placeholder={placeholder(field)}
            value={mappings[field.name] ?? ""}
            onChange={(event) => onChange({ ...mappings, [field.name]: event.target.value })}
          />
          <span className="border border-[#C5CBD3] bg-[#F7F8FA] px-2 py-1 font-mono text-[9px]">{field.type}</span>
        </div>
      ))}
    </section>
  );
}

function textRecord(value: unknown): Record<string, string> {
  const record = recordValue(value) ?? {};
  return Object.fromEntries(
    Object.entries(record).flatMap(([key, item]) =>
      typeof item === "string" ? [[key, item]] : [],
    ),
  );
}

function UseLlmBoard({
  node,
  branchId,
  graph,
  isGraphDirty,
  activeTab,
  onApply,
}: {
  node: PipelineCanvasNode;
  branchId: string | null;
  graph: PipelineGraphV2 | null;
  isGraphDirty: boolean;
  activeTab: BoardTab;
  onApply: (nodeId: string, patch: Record<string, unknown>) => void;
}) {
  const config = nodeDataOf(node);
  const modelParameters = recordValue(config.modelParameters);
  const configuredMediaReferenceField =
    asText(config.mediaReferenceField) ?? "";
  const configuredPromptMode = semanticPromptModeOf(
    config.promptMode,
    Boolean(configuredMediaReferenceField),
  );
  const [templateId, setTemplateId] = useState(
    asText(config.templateId) ?? "empty_prompt",
  );
  const [label, setLabel] = useState(asText(config.label) ?? "Use LLM");
  const [modelAlias, setModelAlias] = useState(
    asText(config.modelAlias) ?? "default-completion",
  );
  const [expectedModelId, setExpectedModelId] = useState(
    asText(config.expectedModelId) ?? "",
  );
  const [expectedModelRevision, setExpectedModelRevision] = useState(
    asText(config.expectedModelRevision) ?? "",
  );
  const [promptVersionId, setPromptVersionId] = useState(
    asText(config.promptVersionId) ?? "draft@1",
  );
  const [promptTemplate, setPromptTemplate] = useState(
    asText(config.promptTemplate) ??
      (configuredPromptMode === "layout_aware_vision"
        ? DEFAULT_LAYOUT_USER_PROMPT
        : ""),
  );
  const [systemPrompt, setSystemPrompt] = useState(
    asText(config.systemPrompt) ??
      (configuredPromptMode === "basic_vision"
        ? DEFAULT_VISION_SYSTEM_PROMPT
        : ""),
  );
  const [inputFields, setInputFields] = useState(
    arrayText(config.inputFields).join(", "),
  );
  const [mediaReferenceField, setMediaReferenceField] = useState(
    configuredMediaReferenceField,
  );
  const [promptMode, setPromptMode] =
    useState<SemanticPromptMode>(configuredPromptMode);
  const [outputColumn, setOutputColumn] = useState(
    asText(config.outputColumn) ?? "interpretation",
  );
  const [outputSchemaText, setOutputSchemaText] = useState(
    JSON.stringify(recordValue(config.outputSchema) ?? {}, null, 2),
  );
  const [dataClassification, setDataClassification] = useState(
    asText(config.dataClassification) ?? "public",
  );
  const [includeErrors, setIncludeErrors] = useState(
    config.outputMode === "with_errors",
  );
  const [skipRecomputingRows, setSkipRecomputingRows] = useState(
    config.skipRecomputingRows !== false,
  );
  const [cacheGeneration, setCacheGeneration] = useState(
    positiveInteger(config.cacheGeneration) ?? 1,
  );
  const [temperature, setTemperature] = useState(
    String(modelParameters?.temperature ?? 0),
  );
  const [maxOutputTokens, setMaxOutputTokens] = useState(
    String(modelParameters?.maxOutputTokens ?? 500),
  );
  const [thinkingMode, setThinkingMode] = useState(
    asText(modelParameters?.thinkingMode) ?? "provider_default",
  );
  const [trialCount, setTrialCount] = useState(
    String(config.trialCount ?? 3),
  );
  const parsedSchema = useMemo(
    () => parseJsonObject(outputSchemaText),
    [outputSchemaText],
  );
  const parsedTrialCount = useMemo(
    () => useLlmTrialCount(trialCount),
    [trialCount],
  );
  const draftConfig = useMemo(
    () =>
      parsedSchema.value && parsedTrialCount
        ? {
            label,
            templateId,
            modelAlias,
            expectedModelId: expectedModelId.trim() || undefined,
            expectedModelRevision: expectedModelRevision.trim() || undefined,
            promptVersionId,
            promptMode,
            promptTemplate,
            systemPrompt: systemPrompt.trim() || undefined,
            inputFields: csvValues(inputFields),
            mediaReferenceField:
              mediaReferenceField.trim() || undefined,
            outputColumn,
            outputSchema: parsedSchema.value,
            dataClassification,
            outputMode: includeErrors ? "with_errors" : "simple",
            skipRecomputingRows,
            cacheGeneration,
            modelParameters: {
              temperature: Number(temperature),
              maxOutputTokens: Number(maxOutputTokens),
              thinkingMode,
            },
            trialCount: parsedTrialCount,
            cachePolicy: "referenced_fields",
          }
        : null,
    [
      dataClassification,
      cacheGeneration,
      includeErrors,
      inputFields,
      label,
      maxOutputTokens,
      mediaReferenceField,
      modelAlias,
      expectedModelId,
      expectedModelRevision,
      outputColumn,
      parsedSchema.value,
      parsedTrialCount,
      promptMode,
      promptTemplate,
      promptVersionId,
      skipRecomputingRows,
      systemPrompt,
      temperature,
      templateId,
      thinkingMode,
    ],
  );
  const trialInvalidReason = useMemo(
    () =>
      useLlmInvalidReason({
        draftConfig,
        inputFields,
        mediaReferenceField,
        modelAlias,
        outputColumn,
        parsedSchemaError: parsedSchema.error,
        promptMode,
        promptTemplate,
        promptVersionId,
        trialCount: parsedTrialCount,
      }),
    [
      draftConfig,
      inputFields,
      mediaReferenceField,
      modelAlias,
      outputColumn,
      parsedSchema.error,
      parsedTrialCount,
      promptMode,
      promptTemplate,
      promptVersionId,
    ],
  );
  const trialGraph = useMemo(
    () => withUseLlmDraftConfiguration(graph, node.id, draftConfig),
    [draftConfig, graph, node.id],
  );

  const chooseTemplate = (nextTemplateId: string) => {
    const template = LLM_TEMPLATES.find((item) => item.id === nextTemplateId);
    if (!template) return;
    setTemplateId(template.id);
    setPromptTemplate(template.prompt);
    setOutputColumn(template.outputColumn);
    setOutputSchemaText(JSON.stringify(template.schema, null, 2));
  };
  const choosePromptMode = (nextPromptMode: string) => {
    if (!isSemanticPromptMode(nextPromptMode)) return;
    setPromptMode(nextPromptMode);
    if (nextPromptMode === "text") {
      setMediaReferenceField("");
    } else if (!mediaReferenceField.trim()) {
      setMediaReferenceField("mediaReference");
    }
    if (
      nextPromptMode === "basic_vision" &&
      !systemPrompt.trim()
    ) {
      setSystemPrompt(DEFAULT_VISION_SYSTEM_PROMPT);
    }
    if (
      nextPromptMode === "layout_aware_vision" &&
      !promptTemplate.trim()
    ) {
      setPromptTemplate(DEFAULT_LAYOUT_USER_PROMPT);
    }
  };
  const handleApply = () => {
    if (!draftConfig || trialInvalidReason) return;
    onApply(node.id, draftConfig);
  };

  if (activeTab === "inputs") {
    return (
      <BoardReadingPane
        title="Dataset-row input only"
        description="Use LLM은 Media Set을 직접 받지 않습니다. 이미지/PDF는 Media → Table rows bridge가 만든 mediaReference 컬럼으로 전달해야 합니다."
      >
        <ContractFlow
          steps={[
            "media_set_selection",
            "Media → Table rows",
            "dataset_version",
            "Use LLM",
          ]}
        />
        <EvidenceCallout>
          direct media edge는 artifact-kind validation에서 거절됩니다. Audio와
          video는 먼저 ASR 또는 frame 추출을 거쳐야 합니다.
        </EvidenceCallout>
      </BoardReadingPane>
    );
  }
  if (activeTab === "output") {
    return (
      <BoardReadingPane
        title="Typed output contract"
        description="모델 응답은 선택한 JSON schema로 파싱되고 output column에 저장됩니다. Include errors는 row 오류를 함께 남깁니다."
      >
        <pre className="overflow-auto border border-[#C5CBD3] bg-[#17212B] p-3 font-mono text-[10px] leading-5 text-[#D7E0EA]">
          {outputSchemaText}
        </pre>
      </BoardReadingPane>
    );
  }

  return (
    <div className="flex min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-5xl space-y-4">
          <BoardTitle
            icon={Sparkles}
            title="Use LLM"
            description="prompt, referenced fields, governed model, typed output, trial, cache policy를 하나의 versioned transform config로 저장합니다."
          />

          <section className="border border-[#C5CBD3] bg-white p-3">
            <SectionHeading
              title="Start from a template"
              description="공개 Pipeline Builder의 기본 prompt template 5개와 별도 Empty prompt입니다."
            />
            <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-6">
              {LLM_TEMPLATES.map((template) => (
                <button
                  key={template.id}
                  type="button"
                  aria-pressed={templateId === template.id}
                  className={cn(
                    "min-h-14 border px-2 py-2 text-left text-[10px]",
                    templateId === template.id
                      ? "border-[#7961DB] bg-[#F1EEFB] text-[#4F3C9A]"
                      : "border-[#C5CBD3] hover:bg-[#F7F8FA]",
                  )}
                  onClick={() => chooseTemplate(template.id)}
                >
                  <div className="font-semibold">{template.label}</div>
                  <div className="mt-1 text-[9px] text-muted-foreground">
                    {template.outputColumn}
                  </div>
                </button>
              ))}
            </div>
          </section>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
            <section className="space-y-3 border border-[#C5CBD3] bg-white p-3">
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="Node name">
                  <Input
                    aria-label="Use LLM node name"
                    value={label}
                    onChange={(event) => setLabel(event.target.value)}
                  />
                </Field>
                <Field label="Prompt version">
                  <Input
                    aria-label="Prompt version"
                    className="font-mono"
                    value={promptVersionId}
                    onChange={(event) => setPromptVersionId(event.target.value)}
                  />
                </Field>
              </div>
              <PromptField
                label="Use LLM instructions"
                value={promptTemplate}
                isLocked={false}
                onChange={setPromptTemplate}
              />
              <PromptField
                label="Use LLM system prompt"
                value={systemPrompt}
                isLocked={false}
                onChange={setSystemPrompt}
              />
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="Referenced input fields">
                  <Input
                    aria-label="Use LLM input fields"
                    className="font-mono"
                    placeholder="text, structure, sourceLocator"
                    value={inputFields}
                    onChange={(event) => setInputFields(event.target.value)}
                  />
                </Field>
                <Field label="Media reference field">
                  <Input
                    aria-label="Use LLM media reference field"
                    className="font-mono"
                    placeholder="mediaReference (optional)"
                    value={mediaReferenceField}
                    onChange={(event) =>
                      setMediaReferenceField(event.target.value)
                    }
                  />
                </Field>
              </div>
              {mediaReferenceField.trim() ? (
                <div className="border border-[#C9C1EA] bg-[#F7F5FF] px-2.5 py-2 text-[10px] leading-4 text-[#5846A5]">
                  Vision mode · 이 컬럼은 Media → Table rows bridge가 만든
                  pinned mediaReference여야 합니다.
                </div>
              ) : null}
            </section>

            <section className="space-y-3 border border-[#C5CBD3] bg-white p-3">
              <Field label="Prompt mode">
                <Select value={promptMode} onValueChange={choosePromptMode}>
                  <SelectTrigger aria-label="Use LLM prompt mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="text">Text</SelectItem>
                    <SelectItem value="basic_vision">
                      Basic vision
                    </SelectItem>
                    <SelectItem value="layout_aware_vision">
                      Layout-aware vision
                    </SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Governed model">
                <Input
                  aria-label="Use LLM model"
                  className="font-mono"
                  value={modelAlias}
                  onChange={(event) => setModelAlias(event.target.value)}
                />
              </Field>
              {expectedModelId && expectedModelRevision ? (
                <div className="border border-[#C9C1EA] bg-[#F7F5FF] px-2.5 py-2 text-[10px] leading-4 text-[#5846A5]">
                  <div className="font-semibold">Promoted resolution pin</div>
                  <div className="mt-1 break-all font-mono">
                    {expectedModelId} @ {expectedModelRevision}
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    className="mt-1.5 h-6 px-1.5 text-[9px]"
                    onClick={() => {
                      setExpectedModelId("");
                      setExpectedModelRevision("");
                    }}
                  >
                    Clear promoted pin
                  </Button>
                </div>
              ) : null}
              <Field label="Data classification">
                <Select
                  value={dataClassification}
                  onValueChange={setDataClassification}
                >
                  <SelectTrigger aria-label="Use LLM data classification">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="public">public</SelectItem>
                    <SelectItem value="internal">internal</SelectItem>
                    <SelectItem value="confidential">
                      confidential
                    </SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Temperature">
                  <Input
                    aria-label="Use LLM temperature"
                    type="number"
                    min={0}
                    max={2}
                    step={0.1}
                    value={temperature}
                    onChange={(event) => setTemperature(event.target.value)}
                  />
                </Field>
                <Field label="Max tokens">
                  <Input
                    aria-label="Use LLM max tokens"
                    type="number"
                    min={1}
                    value={maxOutputTokens}
                    onChange={(event) => setMaxOutputTokens(event.target.value)}
                  />
                </Field>
              </div>
              <Field label="Thinking mode">
                <Select value={thinkingMode} onValueChange={setThinkingMode}>
                  <SelectTrigger aria-label="Use LLM thinking mode">
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
              </Field>
              <Field label="Trial rows · max 50">
                <Input
                  aria-label="Use LLM trial rows"
                  type="number"
                  min={1}
                  max={50}
                  value={trialCount}
                  onChange={(event) => setTrialCount(event.target.value)}
                />
                {parsedTrialCount === null ? (
                  <p className="mt-1 text-[9px] text-destructive">
                    1~50 사이의 정수를 입력하세요.
                  </p>
                ) : null}
              </Field>
              <ToggleRow
                label="Include errors"
                detail="row별 typed error와 correction evidence 포함"
                checked={includeErrors}
                onChange={setIncludeErrors}
              />
              <ToggleRow
                label="Skip recomputing rows"
                detail="동일 Pipeline scope·node·security policy·generation 안에서 model, prompt, input, media, schema가 모두 같을 때 성공한 row를 실제로 재사용합니다."
                checked={skipRecomputingRows}
                onChange={setSkipRecomputingRows}
              />
              <div className="space-y-2 border border-[#C5CBD3] bg-[#F7F8FA] p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <div className="text-[10px] font-semibold">
                      Cache generation {cacheGeneration}
                    </div>
                    <div className="mt-0.5 text-[9px] leading-4 text-muted-foreground">
                      generation을 올리면 이전 결과는 삭제하지 않고 새
                      preview와 build에서 더 이상 읽지 않습니다.
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label="Clear Use LLM row cache"
                    className="h-7 shrink-0 rounded-[2px] px-2 text-[10px]"
                    disabled={cacheGeneration >= MAX_CACHE_GENERATION}
                    onClick={() =>
                      setCacheGeneration((current) => current + 1)
                    }
                  >
                    <RefreshCcw className="size-3" />
                    Clear cache
                  </Button>
                </div>
                <p className="text-[9px] leading-4 text-muted-foreground">
                  Apply configuration과 Save 후 새 generation이 branch
                  변경사항으로 저장됩니다. Preview는 계속 no-commit이며
                  serving output version을 만들지 않습니다.
                </p>
              </div>
            </section>
          </div>

          <section className="border border-[#C5CBD3] bg-white p-3">
            <div className="grid gap-3 lg:grid-cols-[220px_minmax(0,1fr)]">
              <Field label="Output column">
                <Input
                  aria-label="Use LLM output column"
                  className="font-mono"
                  value={outputColumn}
                  onChange={(event) => setOutputColumn(event.target.value)}
                />
              </Field>
              <Field label="Typed output JSON schema">
                <Textarea
                  aria-label="Use LLM output schema"
                  className={cn(
                    "min-h-40 font-mono text-[11px]",
                    parsedSchema.error
                      ? "border-destructive focus-visible:ring-destructive"
                      : null,
                  )}
                  spellCheck={false}
                  value={outputSchemaText}
                  onChange={(event) => setOutputSchemaText(event.target.value)}
                />
              </Field>
            </div>
            {parsedSchema.error ? (
              <p className="mt-2 text-[10px] text-destructive">
                {parsedSchema.error}
              </p>
            ) : null}
          </section>

          <div className="flex justify-end">
            <Button
              className="rounded-[2px]"
              disabled={
                !draftConfig || Boolean(trialInvalidReason)
              }
              onClick={handleApply}
            >
              Apply configuration
            </Button>
          </div>
        </div>
      </div>
      <UseLlmTrialPanel
        branchId={branchId}
        graph={trialGraph}
        nodeId={node.id}
        outputColumn={outputColumn}
        inputFields={csvValues(inputFields)}
        trialCount={parsedTrialCount}
        isGraphDirty={isGraphDirty}
        invalidReason={trialInvalidReason}
      />
    </div>
  );
}

function DocumentProfileSummary({
  strategy,
  promptMode,
  processorId,
  outputFormat,
}: {
  strategy: string;
  promptMode: string;
  processorId: string;
  outputFormat: string;
}) {
  return (
    <aside className="border-l border-[#C5CBD3] bg-white p-3">
      <div className="text-[10px] font-bold tracking-[0.08em] text-muted-foreground uppercase">
        Profile passport
      </div>
      <dl className="mt-3 space-y-2 text-[10px]">
        <SummaryRow label="strategy" value={strategy} />
        <SummaryRow label="processor" value={processorId} />
        <SummaryRow label="format" value={outputFormat} />
        <SummaryRow label="prompt mode" value={promptMode} />
        <SummaryRow label="serving" value="false · preview" />
      </dl>
      <div className="mt-4 border border-[#E2C98B] bg-[#FFF8E7] p-2.5 text-[10px] leading-4 text-[#725B20]">
        현재 no-commit runtime은 processorId를 실행합니다. VLM prompt mode는
        versioned profile 계약으로 저장되며, 해당 runtime이 활성화되기 전에는
        validation-only 상태입니다.
      </div>
      <div className="mt-3 border border-[#BCD9D6] bg-[#F4FAF9] p-2.5 text-[10px] leading-4">
        원본 MediaVersion, page, bbox, processor/model version, security
        envelope를 downstream Content Unit까지 유지합니다.
      </div>
    </aside>
  );
}

function PromptField({
  label,
  value,
  isLocked,
  onChange,
}: {
  label: string;
  value: string;
  isLocked: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label}>
      <div className="relative">
        <Textarea
          aria-label={`${label}${isLocked ? " locked" : ""}`}
          className={cn(
            "min-h-32 font-mono text-[10px]",
            isLocked ? "bg-[#F1F3F5] pr-8 text-muted-foreground" : null,
          )}
          readOnly={isLocked}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        {isLocked ? (
          <LockKeyhole className="absolute top-2 right-2 size-3.5 text-[#738091]" />
        ) : null}
      </div>
    </Field>
  );
}

function BoardReadingPane({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-0 overflow-y-auto p-5">
      <div className="mx-auto max-w-3xl space-y-4">
        <div>
          <h2 className="text-[16px] font-semibold">{title}</h2>
          <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
            {description}
          </p>
        </div>
        {children}
      </div>
    </div>
  );
}

function ContractFlow({ steps }: { steps: readonly string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2 border border-[#C5CBD3] bg-white p-4">
      {steps.map((step, index) => (
        <div key={step} className="contents">
          <span className="border border-[#BCD9D6] bg-[#F4FAF9] px-2 py-1.5 font-mono text-[10px]">
            {step}
          </span>
          {index < steps.length - 1 ? (
            <ArrowRight className="size-3.5 text-muted-foreground" />
          ) : null}
        </div>
      ))}
    </div>
  );
}

function EvidenceCallout({ children }: { children: React.ReactNode }) {
  return (
    <div className="border border-[#BCD9D6] bg-[#F4FAF9] p-3 text-[11px] leading-5">
      {children}
    </div>
  );
}

function BoardTitle({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof Sparkles;
  title: string;
  description: string;
}) {
  return (
    <div className="flex items-start gap-2">
      <div className="grid size-8 shrink-0 place-items-center border border-[#C5CBD3] bg-white">
        <Icon className="size-4 text-[#2D72D2]" />
      </div>
      <div>
        <h2 className="text-[16px] font-semibold">{title}</h2>
        <p className="mt-0.5 text-[11px] leading-5 text-muted-foreground">
          {description}
        </p>
      </div>
    </div>
  );
}

function SectionHeading({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div>
      <h3 className="text-[12px] font-semibold">{title}</h3>
      <p className="mt-0.5 text-[10px] leading-4 text-muted-foreground">
        {description}
      </p>
    </div>
  );
}

function ToggleRow({
  label,
  detail,
  checked,
  onChange,
}: {
  label: string;
  detail: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 border-t border-[#E1E5EA] pt-2">
      <input
        type="checkbox"
        className="mt-0.5 size-3.5 accent-[#7961DB]"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>
        <span className="block text-[10px] font-semibold">{label}</span>
        <span className="mt-0.5 block text-[9px] leading-4 text-muted-foreground">
          {detail}
        </span>
      </span>
    </label>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-[10px] text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[76px_minmax(0,1fr)] gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="break-all font-mono">{value}</dd>
    </div>
  );
}

function inputPortLabel(node: PipelineCanvasNode): string {
  if (["source.stream", "source.geospatial"].includes(node.descriptorId)) {
    return "—";
  }
  if (node.descriptorId === "output.geospatial") {
    return "input : dataset_version | geospatial_series";
  }
  if (
    [
      "transform.document_extract",
      "transform.media",
      "transform.embedding.vision",
    ].includes(node.descriptorId)
  ) {
    return "media : media_set_selection | media_derivative_set";
  }
  return "input : dataset_version";
}

function semanticPromptModeOf(
  value: unknown,
  hasMediaReference: boolean,
): SemanticPromptMode {
  return isSemanticPromptMode(value)
    ? value
    : hasMediaReference
      ? "basic_vision"
      : "text";
}

function isSemanticPromptMode(value: unknown): value is SemanticPromptMode {
  return (
    value === "text" ||
    value === "basic_vision" ||
    value === "layout_aware_vision"
  );
}

function isPromptMode(
  value: string,
): value is Extract<
  SemanticPromptMode,
  "basic_vision" | "layout_aware_vision"
> {
  return value === "basic_vision" || value === "layout_aware_vision";
}

function outputPortLabel(node: PipelineCanvasNode): string {
  if (node.descriptorId === "source.stream") {
    return "stream : stream_checkpoint";
  }
  if (["source.geospatial", "output.geospatial"].includes(node.descriptorId)) {
    return "series : geospatial_series";
  }
  if (node.descriptorId === "transform.document_extract") {
    return "content : content_unit_set";
  }
  if (node.descriptorId === "transform.media") {
    return "derivatives : media_derivative_set";
  }
  if (node.descriptorId === "transform.embedding.vision") {
    return "index : vector_index_generation";
  }
  return "dataset : dataset_version";
}

function useLlmInvalidReason({
  draftConfig,
  inputFields,
  mediaReferenceField,
  modelAlias,
  outputColumn,
  parsedSchemaError,
  promptMode,
  promptTemplate,
  promptVersionId,
  trialCount,
}: {
  draftConfig: Record<string, unknown> | null;
  inputFields: string;
  mediaReferenceField: string;
  modelAlias: string;
  outputColumn: string;
  parsedSchemaError: string | null;
  promptMode: SemanticPromptMode;
  promptTemplate: string;
  promptVersionId: string;
  trialCount: number | null;
}): string | null {
  if (parsedSchemaError) return parsedSchemaError;
  if (trialCount === null) return "Trial rows는 1~50 사이의 정수여야 합니다.";
  if (!modelAlias.trim()) return "Governed model alias를 입력하세요.";
  if (!promptVersionId.trim()) return "Prompt version을 입력하세요.";
  if (!promptTemplate.trim()) return "Use LLM instructions를 입력하세요.";
  if (csvValues(inputFields).length === 0) {
    return "모델 요청에 전달할 Referenced input fields를 한 개 이상 입력하세요.";
  }
  if (!outputColumn.trim()) return "Typed output을 저장할 column 이름을 입력하세요.";
  if (promptMode === "text" && mediaReferenceField.trim()) {
    return "Text mode에서는 Media reference field를 비워야 합니다.";
  }
  if (promptMode !== "text" && !mediaReferenceField.trim()) {
    return "Vision mode에서는 pinned Media reference field가 필요합니다.";
  }
  return draftConfig ? null : "현재 form draft를 Graph v2 config로 만들 수 없습니다.";
}

function parseJsonObject(value: string): {
  value: Record<string, unknown> | null;
  error: string | null;
} {
  try {
    const parsed = JSON.parse(value) as unknown;
    const record = recordValue(parsed);
    return record
      ? { value: record, error: null }
      : { value: null, error: "JSON schema는 object여야 합니다." };
  } catch {
    return { value: null, error: "유효한 JSON schema를 입력하세요." };
  }
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function arrayText(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function positiveInteger(value: unknown): number | null {
  return typeof value === "number" &&
    Number.isInteger(value) &&
    value > 0 &&
    value <= MAX_CACHE_GENERATION
    ? value
    : null;
}

function csvValues(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

const DOCUMENT_STRATEGIES = [
  { id: "raw", label: "Raw", detail: "embedded text" },
  { id: "ocr", label: "OCR", detail: "image text" },
  { id: "layout_aware", label: "Layout-aware", detail: "bbox + blocks" },
  { id: "basic_vision", label: "Basic VLM", detail: "editable user" },
  {
    id: "layout_aware_vision",
    label: "Layout VLM",
    detail: "editable system",
  },
] as const;

const DEFAULT_VISION_SYSTEM_PROMPT =
  "Use the platform safety policy and return only output that satisfies the selected typed schema.";
const DEFAULT_LAYOUT_USER_PROMPT =
  "Extract layout-preserving JSON blocks with page, boundingBox, blockType, confidence, text, tableHtml, language, parentId, and readingOrder.";

const DOCUMENT_OUTPUT_SAMPLE = `{
  "sourceMediaItemVersionId": "miv-...",
  "unitKind": "layout_region",
  "text": "Payment terms",
  "structure": { "role": "heading", "level": 1 },
  "sourceLocator": {
    "pageNumber": 1,
    "bbox": { "x": 0.12, "y": 0.08, "width": 0.42, "height": 0.06 }
  },
  "confidence": 0.98,
  "securityEnvelope": { "classification": "confidential" }
}`;

const LLM_TEMPLATES = [
  {
    id: "classification",
    label: "Classification",
    prompt: "Classify {{text}} into one of the permitted categories.",
    outputColumn: "classification",
    schema: {
      type: "object",
      properties: { label: { type: "string" } },
      required: ["label"],
    },
  },
  {
    id: "sentiment",
    label: "Sentiment",
    prompt: "Determine the sentiment of {{text}}.",
    outputColumn: "sentiment",
    schema: {
      type: "object",
      properties: { sentiment: { type: "string" } },
      required: ["sentiment"],
    },
  },
  {
    id: "summarization",
    label: "Summarization",
    prompt: "Summarize {{text}} without adding unsupported claims.",
    outputColumn: "summary",
    schema: {
      type: "object",
      properties: { summary: { type: "string" } },
      required: ["summary"],
    },
  },
  {
    id: "entity_extraction",
    label: "Entity extraction",
    prompt: "Extract named entities from {{text}}.",
    outputColumn: "entities",
    schema: {
      type: "object",
      properties: { entities: { type: "array", items: { type: "string" } } },
      required: ["entities"],
    },
  },
  {
    id: "translation",
    label: "Translation",
    prompt: "Translate {{text}} while preserving names and numeric values.",
    outputColumn: "translation",
    schema: {
      type: "object",
      properties: { translation: { type: "string" } },
      required: ["translation"],
    },
  },
  {
    id: "empty_prompt",
    label: "Empty prompt",
    prompt: "Interpret {{text}}.",
    outputColumn: "interpretation",
    schema: {
      type: "object",
      properties: { result: { type: "string" } },
      required: ["result"],
    },
  },
] as const;
