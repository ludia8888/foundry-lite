import type {
  FoundryLiteApiError,
  MediaProcessorDescriptor,
  PipelineNodeDescriptorPayload,
  TrainedModelDescriptor,
} from "@foundry-lite/sdk";
import {
  ArrowRight,
  Cpu,
  ExternalLink,
  FileText,
  Sparkles,
} from "lucide-react";
import type { ReactNode } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

import {
  configFieldKey,
  descriptorConfigFields,
  descriptorLabel,
  descriptorState,
  portLabel,
} from "../pipeline-catalog-model";
import {
  compactJson,
  recordList,
  recordValue,
  textValue,
  type PreviewRecord,
} from "../pipeline-preview-model";

interface PipelineCapabilityDetailsProps {
  descriptor: PipelineNodeDescriptorPayload | null;
  processorData: {
    available: boolean;
    items: MediaProcessorDescriptor[];
  } | null;
  processorError: FoundryLiteApiError | null;
  isProcessorLoading: boolean;
  trainedModelData: {
    available: boolean;
    items: TrainedModelDescriptor[];
    count: number;
  } | null;
  trainedModelError: FoundryLiteApiError | null;
  importedTrainedModelRefs: readonly string[];
  trainedModelUsageByRef: Readonly<Record<string, readonly string[]>>;
  isTrainedModelLoading: boolean;
  hasOutputNode: boolean;
  onRetryProcessors: () => void;
  onRetryTrainedModels: () => void;
  onImportTrainedModel: (modelRef: string) => void;
  onRemoveTrainedModel: (modelRef: string) => void;
  onAdd: () => void;
}

export function PipelineCapabilityDetails({
  descriptor,
  processorData,
  processorError,
  isProcessorLoading,
  trainedModelData,
  trainedModelError,
  importedTrainedModelRefs,
  trainedModelUsageByRef,
  isTrainedModelLoading,
  hasOutputNode,
  onRetryProcessors,
  onRetryTrainedModels,
  onImportTrainedModel,
  onRemoveTrainedModel,
  onAdd,
}: PipelineCapabilityDetailsProps) {
  if (!descriptor) {
    return (
      <div className="grid place-items-center p-6 text-[12px] text-muted-foreground">
        노드를 선택하면 named port와 실행 경계를 확인할 수 있습니다.
      </div>
    );
  }
  const state = descriptorState(
    descriptor,
    hasOutputNode,
    importedTrainedModelRefs.length > 0,
  );
  const descriptorRecord = descriptor as PreviewRecord;
  const configFields = descriptorConfigFields(descriptor);
  const processorRequired = configFields.some(
    (field) => textValue(field.fieldName) === "processorId",
  );

  return (
    <ScrollArea className="min-h-0">
      <div className="space-y-4 p-4">
        <DescriptorHeading descriptor={descriptor} isAddable={state.isAddable} />
        <div
          className={cn(
            "rounded-[2px] border px-3 py-2 text-[11px] leading-5",
            state.isAddable
              ? "border-success/30 bg-success/5"
              : "border-warning/30 bg-warning/5",
          )}
        >
          {state.reason}
        </div>

        <PublicWorkflowContract descriptorId={descriptor.descriptorId} />

        <DetailSection title="Runtime contract">
          <DetailField label="kind" value={descriptor.kind} />
          <DetailField label="availability" value={descriptor.availability} />
          <DetailField
            label="runtime"
            value={textValue(descriptorRecord.runtimeCapability) ?? "-"}
          />
        </DetailSection>

        <DetailSection title="Named ports">
          <PortList
            label="inputs"
            ports={recordList(descriptorRecord.inputPorts)}
          />
          <PortList
            label="outputs"
            ports={recordList(descriptorRecord.outputPorts)}
          />
        </DetailSection>

        <ConfigurationFields fields={configFields} />

        {processorRequired ? (
          <ProcessorRegistryEvidence
            data={processorData}
            error={processorError}
            isLoading={isProcessorLoading}
            onRetry={onRetryProcessors}
          />
        ) : null}

        {descriptor.descriptorId === "transform.trained_model" ? (
          <TrainedModelRegistryEvidence
            data={trainedModelData}
            error={trainedModelError}
            importedModelRefs={importedTrainedModelRefs}
            usageByRef={trainedModelUsageByRef}
            isLoading={isTrainedModelLoading}
            onRetry={onRetryTrainedModels}
            onImport={onImportTrainedModel}
            onRemove={onRemoveTrainedModel}
          />
        ) : null}

        <Button
          className="w-full rounded-[2px]"
          disabled={!state.isAddable}
          onClick={onAdd}
        >
          {state.isAddable ? "이 노드를 그래프에 추가" : "현재 추가할 수 없음"}
        </Button>
      </div>
    </ScrollArea>
  );
}

const LLM_TEMPLATES = [
  "Classification",
  "Sentiment",
  "Summarization",
  "Entity extraction",
  "Translation",
  "Empty prompt",
] as const;

function PublicWorkflowContract({ descriptorId }: { descriptorId: string }) {
  if (descriptorId === "transform.media") {
    return (
      <DetailSection title="공개 동작 기준 · Transform media">
        <div className="border border-[#C5CBD3] bg-[#F4F8FD] p-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-semibold text-[#215DB0]">
            <Cpu className="size-3.5" />
            Exact processor pin · no silent fallback
          </div>
          <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
            이미지 OCR·metadata, audio ASR, video probe·scene frame·scene
            vision을 같은 media input port에서 선택합니다. 실행 시 processor와
            version을 registry에서 다시 resolve하고, 형식이나 runtime이 맞지 않으면
            typed failure로 끝납니다.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1 text-[10px]">
            <WorkflowStep>media selection</WorkflowStep>
            <ArrowRight className="size-3 text-muted-foreground" />
            <WorkflowStep>processor@version</WorkflowStep>
            <ArrowRight className="size-3 text-muted-foreground" />
            <WorkflowStep>media derivatives</WorkflowStep>
          </div>
        </div>
      </DetailSection>
    );
  }
  if (descriptorId === "transform.embedding.vision") {
    return (
      <DetailSection title="공개 동작 기준 · Vision embedding">
        <div className="border border-[#C5CBD3] bg-[#F7F5FF] p-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-semibold text-[#634DBF]">
            <Sparkles className="size-3.5" />
            Shared image/text vector space
          </div>
          <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
            이미지 또는 frame derivative를 visual index generation으로 연결합니다.
            현재는 Graph v2 작성·named-port 검증만 가능하고 no-commit preview
            executor에는 handler가 없으므로 실행 가능하다고 표시하지 않습니다.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1 text-[10px]">
            <WorkflowStep>media | derivatives</WorkflowStep>
            <ArrowRight className="size-3 text-muted-foreground" />
            <WorkflowStep>clip-ViT-B-32</WorkflowStep>
            <ArrowRight className="size-3 text-muted-foreground" />
            <WorkflowStep>vision index</WorkflowStep>
          </div>
        </div>
      </DetailSection>
    );
  }
  if (descriptorId === "transform.use_llm") {
    return (
      <DetailSection title="공개 동작 기준 · Use LLM board">
        <div className="border border-[#C5CBD3] bg-[#F7F5FF] p-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-semibold text-[#634DBF]">
            <Sparkles className="size-3.5" />
            MediaReference 기반 의미 해석
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1 text-[10px]">
            <WorkflowStep>Media Set</WorkflowStep>
            <ArrowRight className="size-3 text-muted-foreground" />
            <WorkflowStep>Media → Table rows</WorkflowStep>
            <ArrowRight className="size-3 text-muted-foreground" />
            <WorkflowStep>mediaReference</WorkflowStep>
            <ArrowRight className="size-3 text-muted-foreground" />
            <WorkflowStep>Use LLM</WorkflowStep>
          </div>
          <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
            Use LLM은 Media Set을 직접 받지 않습니다. mediaReference 컬럼이 있는
            테이블을 입력으로 받고, vision model·prompt·typed output을 함께
            고정합니다.
          </p>
          <div className="mt-2 flex flex-wrap gap-1">
            {LLM_TEMPLATES.map((template) => (
              <span
                key={template}
                className="border border-[#C9C1EA] bg-white px-1.5 py-0.5 text-[9px] text-[#5846A5]"
              >
                {template}
              </span>
            ))}
          </div>
          <p className="mt-2 text-[10px] leading-4">
            필수 board 계약: prompt + input fields + model + typed output ·
            Include errors · Skip recomputing rows · Input / Output / Trial /
            Errors tabs
          </p>
        </div>
      </DetailSection>
    );
  }
  if (descriptorId === "transform.trained_model") {
    return (
      <DetailSection title="공개 동작 기준 · Trained Model">
        <div className="border border-[#C5CBD3] bg-[#F7F8FA] p-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-semibold text-[#394B59]">
            <Cpu className="size-3.5" />
            Batch model API mapping
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1 text-[10px]">
            <WorkflowStep>single table</WorkflowStep>
            <ArrowRight className="size-3 text-muted-foreground" />
            <WorkflowStep>input expressions</WorkflowStep>
            <ArrowRight className="size-3 text-muted-foreground" />
            <WorkflowStep>model API</WorkflowStep>
            <ArrowRight className="size-3 text-muted-foreground" />
            <WorkflowStep>output aliases</WorkflowStep>
          </div>
          <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
            Reusables에 가져온 모델만 선택할 수 있습니다. build 시 선택 branch와
            fallback에서 최신 모델을 해석해 실행 계획에 고정합니다. 배치만
            지원하며 warm pool과 node preview는 지원하지 않습니다.
          </p>
          <div className="mt-2 flex gap-1">
            <StatusPill intent="info">Batch only</StatusPill>
            <StatusPill intent="warning">Preview unavailable</StatusPill>
            <StatusPill intent="neutral">1 table in · 1 table out</StatusPill>
          </div>
        </div>
      </DetailSection>
    );
  }
  if (descriptorId === "transform.document_extract") {
    return (
      <DetailSection title="공개 동작 기준 · Document Intelligence">
        <div className="border border-[#C5CBD3] bg-[#F4F8FD] p-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-semibold text-[#215DB0]">
            <FileText className="size-3.5" />
            별도 Lab에서 검증한 immutable extraction profile
          </div>
          <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
            Lab은 좌측 문서 썸네일, 중앙 PDF+bbox, 우측 config/result로
            구성됩니다. raw·OCR·layout OCR·VLM을 품질·시간·token 기준으로
            비교하고 선택한 설정을 버전화합니다.
          </p>
          <div className="mt-2 grid grid-cols-2 gap-px border border-[#C5CBD3] bg-[#C5CBD3] text-[9px]">
            <WorkflowRule
              title="Basic VLM"
              detail="user prompt 편집 · system prompt 고정"
            />
            <WorkflowRule
              title="Layout-aware VLM"
              detail="system prompt 편집 · user prompt 고정"
            />
          </div>
          <p className="mt-2 text-[10px] leading-4">
            Pipeline의 document.extract 노드는 Lab 자체를 복제하지 않고, 승인한
            profile과 processor/model version을 정확히 참조해야 합니다.
          </p>
          <Button
            asChild
            size="sm"
            variant="outline"
            className="mt-2 h-7 w-full rounded-[2px] bg-white text-[10px]"
          >
            <a href="/document-intelligence">
              Document Intelligence Lab 열기
              <ExternalLink className="size-3" />
            </a>
          </Button>
        </div>
      </DetailSection>
    );
  }
  if (descriptorId === "bridge.media_to_table_rows") {
    return (
      <DetailSection title="공개 동작 기준 · Media bridge">
        <p className="border border-[#C5CBD3] bg-[#F7F8FA] p-2.5 text-[10px] leading-4">
          Media Set의 각 항목을 테이블 행으로 변환하고 mediaReference 컬럼을
          만듭니다. 이미지·PDF를 vision prompt로 해석하는 Use LLM 노드의
          선행 단계입니다.
        </p>
      </DetailSection>
    );
  }
  return null;
}

function WorkflowStep({ children }: { children: ReactNode }) {
  return (
    <span className="border border-[#C9C1EA] bg-white px-1.5 py-0.5 font-mono text-[9px]">
      {children}
    </span>
  );
}

function WorkflowRule({
  title,
  detail,
}: {
  title: string;
  detail: string;
}) {
  return (
    <div className="bg-white p-2">
      <div className="font-semibold">{title}</div>
      <div className="mt-0.5 leading-4 text-muted-foreground">{detail}</div>
    </div>
  );
}

function DescriptorHeading({
  descriptor,
  isAddable,
}: {
  descriptor: PipelineNodeDescriptorPayload;
  isAddable: boolean;
}) {
  return (
    <div className="flex items-start gap-2">
      <div>
        <h3 className="text-[15px] font-bold">
          {descriptorLabel(descriptor.descriptorId)}
        </h3>
        <p className="font-mono text-[10px] text-muted-foreground">
          {descriptor.descriptorId}@{descriptor.specVersion}
        </p>
      </div>
      <StatusPill
        intent={isAddable ? "success" : "warning"}
        className="ml-auto"
      >
        {isAddable ? "현재 캔버스에서 추가 가능" : "추가 불가"}
      </StatusPill>
    </div>
  );
}

function ConfigurationFields({
  fields,
}: {
  fields: readonly PreviewRecord[];
}) {
  return (
    <DetailSection title="Configuration">
      {fields.length === 0 ? (
        <p className="text-[10px] text-muted-foreground">
          별도 config field가 없습니다.
        </p>
      ) : (
        fields.map((field) => (
          <div
            key={configFieldKey(field)}
            className="flex items-center gap-2 rounded-[2px] border px-2 py-1 text-[10px]"
          >
            <span className="font-mono font-semibold">
              {textValue(field.fieldName) ?? "-"}
            </span>
            <span className="text-muted-foreground">
              {textValue(field.valueKind) ?? "unknown"}
            </span>
            {field.required === true ? (
              <StatusPill intent="warning" className="ml-auto">
                required
              </StatusPill>
            ) : null}
          </div>
        ))
      )}
    </DetailSection>
  );
}

function ProcessorRegistryEvidence({
  data,
  error,
  isLoading,
  onRetry,
}: {
  data: {
    available: boolean;
    items: MediaProcessorDescriptor[];
  } | null;
  error: FoundryLiteApiError | null;
  isLoading: boolean;
  onRetry: () => void;
}) {
  if (isLoading) return <LoadingState rowCount={3} />;
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  const dataRecord = recordValue(data);
  const reason = textValue(dataRecord?.reason);
  return (
    <DetailSection title="Media processor registry">
      {!data?.available ? (
        <div className="rounded-[2px] border border-warning/30 bg-warning/5 p-2 text-[10px]">
          {reason ?? "이 runtime에는 processor registry가 구성되지 않았습니다."}
        </div>
      ) : (
        <div className="space-y-1.5">
          {data.items.map((processor) => (
            <ProcessorCard key={processor.processorId} processor={processor} />
          ))}
        </div>
      )}
    </DetailSection>
  );
}

function ProcessorCard({
  processor,
}: {
  processor: MediaProcessorDescriptor;
}) {
  const record = processor as PreviewRecord;
  const model = recordValue(record.model);
  return (
    <div className="rounded-[2px] border border-[#BCD9D6] bg-[#F4FAF9] p-2">
      <div className="flex items-center gap-2">
        <Cpu className="size-3.5 text-[#147D75]" />
        <span className="font-mono text-[10px] font-bold">
          {processor.processorId}
        </span>
        {record.deterministic === true ? (
          <StatusPill intent="success" className="ml-auto">
            deterministic
          </StatusPill>
        ) : null}
      </div>
      <p className="mt-1 font-mono text-[10px] text-muted-foreground">
        input={processor.inputFormats.join(", ") || "-"} · output=
        {processor.outputKinds.join(", ") || "-"}
      </p>
      <p className="mt-1 font-mono text-[10px] text-muted-foreground">
        model={textValue(model?.name) ?? "-"}@
        {textValue(model?.version) ?? "-"}
      </p>
    </div>
  );
}

function TrainedModelRegistryEvidence({
  data,
  error,
  importedModelRefs,
  usageByRef,
  isLoading,
  onRetry,
  onImport,
  onRemove,
}: {
  data: {
    available: boolean;
    items: TrainedModelDescriptor[];
    count: number;
  } | null;
  error: FoundryLiteApiError | null;
  importedModelRefs: readonly string[];
  usageByRef: Readonly<Record<string, readonly string[]>>;
  isLoading: boolean;
  onRetry: () => void;
  onImport: (modelRef: string) => void;
  onRemove: (modelRef: string) => void;
}) {
  if (isLoading) return <LoadingState rowCount={2} />;
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  return (
    <DetailSection title="Reusables · Trained Models">
      {(data?.items ?? []).map((model) => (
        <div
          key={`${model.modelRef}:${model.revision}`}
          className="border border-[#C5CBD3] bg-white p-2 text-[10px]"
        >
          <div className="flex items-center gap-2">
            <span className="font-semibold">{model.displayName}</span>
            {importedModelRefs.includes(model.modelRef) ? (
              <div className="ml-auto flex items-center gap-1.5">
                <StatusPill intent="success">imported</StatusPill>
                <Button
                  variant="outline"
                  className="h-6 rounded-[2px] px-2 text-[9px]"
                  disabled={(usageByRef[model.modelRef]?.length ?? 0) > 0}
                  title={
                    (usageByRef[model.modelRef]?.length ?? 0) > 0
                      ? `Used by ${usageByRef[model.modelRef]?.join(", ")}`
                      : "Remove this model from Pipeline Reusables"
                  }
                  onClick={() => onRemove(model.modelRef)}
                >
                  {(usageByRef[model.modelRef]?.length ?? 0) > 0
                    ? `Used by ${usageByRef[model.modelRef]?.length} node`
                    : "Remove from pipeline"}
                </Button>
              </div>
            ) : (
              <Button
                variant="outline"
                className="ml-auto h-6 rounded-[2px] px-2 text-[9px]"
                onClick={() => onImport(model.modelRef)}
              >
                Import to pipeline
              </Button>
            )}
          </div>
          <div className="mt-1 font-mono text-[9px] text-muted-foreground">
            {model.modelRef} · {model.branch} → {model.latestVersion}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-px border border-[#D5DAE0] bg-[#D5DAE0]">
            <div className="bg-[#F7F8FA] p-1.5">
              input · {model.inputSchema.map((field) => field.name).join(", ")}
            </div>
            <div className="bg-[#F7F8FA] p-1.5">
              output · {model.outputSchema.map((field) => field.name).join(", ")}
            </div>
          </div>
        </div>
      ))}
      {importedModelRefs.length === 0 ? (
        <p className="border border-[#D9822B] bg-[#FFF4E8] p-2 text-[10px] text-[#7A4314]">
          가져온 모델이 없어 이 노드를 추가할 수 없습니다. 모델을 먼저
          Pipeline Reusables에 import하세요.
        </p>
      ) : null}
    </DetailSection>
  );
}

function DetailSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-1.5">
      <h4 className="text-[10px] font-bold tracking-[0.08em] text-muted-foreground uppercase">
        {title}
      </h4>
      {children}
    </section>
  );
}

function DetailField({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[90px_minmax(0,1fr)] gap-2 text-[10px]">
      <span className="text-muted-foreground">{label}</span>
      <span className="break-all font-mono">{value}</span>
    </div>
  );
}

function PortList({
  label,
  ports,
}: {
  label: string;
  ports: readonly PreviewRecord[];
}) {
  return (
    <div className="grid grid-cols-[58px_minmax(0,1fr)] gap-2 text-[10px]">
      <span className="text-muted-foreground">{label}</span>
      <div className="flex flex-wrap gap-1">
        {ports.length === 0 ? (
          <span className="font-mono">-</span>
        ) : (
          ports.map((port) => (
            <span
              key={compactJson(port)}
              className="rounded-[2px] border bg-muted/30 px-1.5 py-0.5 font-mono"
            >
              {portLabel(port)}
            </span>
          ))
        )}
      </div>
    </div>
  );
}
