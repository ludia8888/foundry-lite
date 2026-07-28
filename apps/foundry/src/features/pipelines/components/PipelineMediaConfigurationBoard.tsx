import {
  ArrowRight,
  AudioLines,
  Eye,
  Images,
  ScanText,
  Video,
} from "lucide-react";
import { useMemo, useState } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import {
  asText,
  nodeDataOf,
  type PipelineCanvasNode,
} from "../pipeline-model";

type BoardTab = "configuration" | "inputs" | "output";

interface MediaBoardProps {
  node: PipelineCanvasNode;
  activeTab: BoardTab;
  onApply: (nodeId: string, patch: Record<string, unknown>) => void;
}

type ProcessorPreset = {
  id: string;
  label: string;
  processorId: string;
  profile: string;
  inputFormats: string;
  outputKind: string;
  model: string;
  detail: string;
  isDeterministic: boolean;
};

const PROCESSOR_PRESETS: readonly ProcessorPreset[] = [
  {
    id: "image_ocr",
    label: "Image OCR",
    processorId: "ocr_v1@1",
    profile: "ocr-tesseract",
    inputFormats: "png · jpg · jpeg · tif · tiff · bmp · webp",
    outputKind: "ocr_v1",
    model: "tesseract@runtime",
    detail: "이미지의 글자를 page content unit으로 추출합니다.",
    isDeterministic: true,
  },
  {
    id: "image_metadata",
    label: "Image metadata",
    processorId: "image_v1@1",
    profile: "image-pillow",
    inputFormats: "png · jpg · jpeg · tif · tiff · bmp · webp",
    outputKind: "thumbnail",
    model: "pillow@runtime",
    detail: "format·mode·크기와 deterministic thumbnail 명세를 만듭니다.",
    isDeterministic: true,
  },
  {
    id: "audio_asr",
    label: "Audio ASR",
    processorId: "asr_v1@1",
    profile: "asr-whisper",
    inputFormats: "wav · mp3 · m4a · flac · ogg · webm · mp4",
    outputKind: "asr_v1",
    model: "whisper@tiny",
    detail: "음성을 timecode가 있는 transcript segment로 변환합니다.",
    isDeterministic: true,
  },
  {
    id: "video_probe",
    label: "Video probe",
    processorId: "video_probe_v1@1",
    profile: "ffprobe",
    inputFormats: "mp4 · mov · mkv · webm · avi · m4v",
    outputKind: "video_probe",
    model: "ffprobe@runtime",
    detail: "codec·duration·frame rate 등 컨테이너 정보를 검사합니다.",
    isDeterministic: true,
  },
  {
    id: "scene_frames",
    label: "Scene frames",
    processorId: "video_frames_v1@1",
    profile: "video-scene-frames",
    inputFormats: "mp4 · mov · mkv · webm · avi · m4v",
    outputKind: "video_scene_frames",
    model: "ffmpeg+tesseract@runtime",
    detail: "장면 프레임과 frame OCR 단위를 추출합니다.",
    isDeterministic: true,
  },
  {
    id: "scene_vision",
    label: "Scene vision",
    processorId: "video_vision_v1@1",
    profile: "video-scene-vision",
    inputFormats: "mp4 · mov · mkv · webm · avi · m4v",
    outputKind: "video_scene_vision",
    model: "clip-ViT-B-32@runtime",
    detail: "장면 프레임을 visual embedding이 포함된 derivative로 만듭니다.",
    isDeterministic: false,
  },
] as const;

export function MediaTransformBoard({
  node,
  activeTab,
  onApply,
}: MediaBoardProps) {
  const config = nodeDataOf(node);
  const configuredBounds = recordValue(config.processingBounds);
  const [label, setLabel] = useState(
    asText(config.label) ?? "Transform media",
  );
  const [processorId, setProcessorId] = useState(
    asText(config.processorId) ?? "image_v1@1",
  );
  const [parametersText, setParametersText] = useState(
    JSON.stringify(recordValue(config.parameters) ?? {}, null, 2),
  );
  const [maxDurationSeconds, setMaxDurationSeconds] = useState(
    String(positiveInteger(configuredBounds?.maxDurationMs) / 1000 || 60),
  );
  const [maxSceneCount, setMaxSceneCount] = useState(
    String(positiveInteger(configuredBounds?.maxSceneCount) || 12),
  );
  const parameters = useMemo(
    () => parseJsonObject(parametersText),
    [parametersText],
  );
  const selectedPreset = PROCESSOR_PRESETS.find(
    (preset) => preset.processorId === processorId,
  );
  const isKnownPresetParameterValid =
    !selectedPreset ||
    (parameters.value !== null && Object.keys(parameters.value).length === 0);
  const boundsCapability = processorBoundsCapability(processorId);
  const durationSeconds = positiveNumber(maxDurationSeconds);
  const sceneCount = positiveIntegerText(maxSceneCount);
  const isProcessingBoundsValid =
    (!boundsCapability.hasDuration || durationSeconds !== null) &&
    (!boundsCapability.hasSceneCount || sceneCount !== null);

  if (activeTab === "inputs") {
    return (
      <ContractPane
        title="Media artifact input"
        description="Committed Media Set selection 또는 앞선 media derivative를 받습니다. 파일을 테이블 행으로 미리 평탄화하지 않습니다."
      >
        <ContractFlow
          steps={[
            "media_set_selection | media_derivative_set",
            processorId,
            "media_derivative_set",
          ]}
        />
        <ContractNote>
          선택한 pin이 지원하는 형식만 처리됩니다. 현재 선택:
          {" "}
          <strong>{selectedPreset?.inputFormats ?? "server registry에서 확인"}</strong>.
          형식 불일치나 설치되지 않은 binary/model은 no-commit preview에서 typed
          failure로 끝나며 다른 processor로 자동 대체되지 않습니다.
        </ContractNote>
      </ContractPane>
    );
  }

  if (activeTab === "output") {
    return (
      <ContractPane
        title="Immutable media derivative output"
        description="출력은 serving Media Set이 아니라 현재 preview run 안의 media_derivative_set입니다."
      >
        <pre className="overflow-auto border border-[#C5CBD3] bg-[#17212B] p-3 font-mono text-[10px] leading-5 text-[#D7E0EA]">
          {mediaDerivativeSample(processorId, selectedPreset?.outputKind)}
        </pre>
        <ContractNote>
          source MediaVersion, processor/model pin, spec hash, content hash,
          security envelope를 유지합니다. no-commit preview는 새 serving version을
          만들지 않습니다.
        </ContractNote>
      </ContractPane>
    );
  }

  const choosePreset = (preset: ProcessorPreset) => {
    setProcessorId(preset.processorId);
    setParametersText("{}");
    setMaxDurationSeconds("60");
    setMaxSceneCount("12");
  };
  const handleApply = () => {
    if (!parameters.value) return;
    onApply(node.id, {
      label,
      processorId,
      parameters: parameters.value,
      processingBounds: processingBoundsPatch(
        boundsCapability,
        durationSeconds,
        sceneCount,
      ),
    });
  };
  const isProcessorPinValid =
    /^[a-z0-9_]+@[0-9]+(?:\.[0-9]+){0,2}$/.test(processorId);

  return (
    <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_286px]">
      <div className="min-h-0 overflow-y-auto p-4">
        <div className="mx-auto max-w-5xl space-y-4">
          <BoardTitle
            icon={Images}
            title="Transform media"
            description="이미지·오디오·영상 processor를 정확한 version pin으로 고정하고, 같은 named-port graph에서 derivative를 다음 노드로 전달합니다."
          />

          <section className="border border-[#C5CBD3] bg-white p-3">
            <SectionHeading
              title="Processor preset"
              description="현재 서버 registry에 등록된 processor identity를 기준으로 한 작성 preset입니다. 실제 preview 시 registry와 설치 상태를 다시 검사합니다."
            />
            <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {PROCESSOR_PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  aria-label={`Use ${preset.label} processor preset`}
                  aria-pressed={processorId === preset.processorId}
                  className={cn(
                    "min-h-32 border p-2.5 text-left",
                    processorId === preset.processorId
                      ? "border-[#2D72D2] bg-[#EAF2FC]"
                      : "border-[#C5CBD3] hover:bg-[#F7F8FA]",
                  )}
                  onClick={() => choosePreset(preset)}
                >
                  <div className="flex items-center gap-2">
                    <PresetIcon presetId={preset.id} />
                    <span className="text-[11px] font-semibold">
                      {preset.label}
                    </span>
                    <StatusPill
                      intent={preset.isDeterministic ? "success" : "warning"}
                      className="ml-auto"
                    >
                      {preset.isDeterministic ? "deterministic" : "model-bound"}
                    </StatusPill>
                  </div>
                  <div className="mt-2 font-mono text-[9px] text-[#245B8F]">
                    {preset.processorId}
                  </div>
                  <p className="mt-1 text-[9px] leading-4 text-muted-foreground">
                    {preset.detail}
                  </p>
                  <div className="mt-2 border-t border-[#D5DAE0] pt-1 font-mono text-[8px] text-muted-foreground">
                    {preset.profile} → {preset.outputKind}
                  </div>
                </button>
              ))}
            </div>
          </section>

          <section className="grid gap-3 border border-[#C5CBD3] bg-white p-3 md:grid-cols-2">
            <Field label="Node name">
              <Input
                aria-label="Transform media node name"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </Field>
            <Field label="Exact processor pin">
              <Input
                aria-label="Media processor pin"
                className="font-mono"
                value={processorId}
                onChange={(event) => setProcessorId(event.target.value)}
              />
            </Field>
            <div className="md:col-span-2">
              <Field label="Processor parameters">
                <Textarea
                  aria-label="Media processor parameters"
                  className={cn(
                    "min-h-28 font-mono text-[10px]",
                    parameters.error ? "border-destructive" : null,
                  )}
                  spellCheck={false}
                  value={parametersText}
                  onChange={(event) => setParametersText(event.target.value)}
                />
              </Field>
              {parameters.error ? (
                <p className="mt-1 text-[10px] text-destructive">
                  {parameters.error}
                </p>
              ) : selectedPreset && !isKnownPresetParameterValid ? (
                <p className="mt-1 text-[10px] text-destructive">
                  이 preset의 현재 parameter schema는 빈 object만 허용합니다.
                </p>
              ) : null}
            </div>
          </section>

          {boundsCapability.hasDuration ? (
            <section
              aria-label="Media preview processing bounds"
              className="border border-[#C5CBD3] bg-white p-3"
            >
              <SectionHeading
                title="Bounded preview execution"
                description="미리보기 비용과 대기 시간을 제한하는 실제 processor 실행 경계입니다. 서버 cap보다 작은 값은 그대로 적용하고, 큰 값은 서버가 다시 낮춥니다."
              />
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field label="Maximum duration (seconds)">
                  <Input
                    type="number"
                    min={0.001}
                    step={0.001}
                    aria-label="Maximum media preview duration seconds"
                    className="font-mono"
                    value={maxDurationSeconds}
                    onChange={(event) =>
                      setMaxDurationSeconds(event.target.value)
                    }
                  />
                </Field>
                {boundsCapability.hasSceneCount ? (
                  <Field label="Maximum scene frames">
                    <Input
                      type="number"
                      min={1}
                      step={1}
                      aria-label="Maximum media preview scene count"
                      className="font-mono"
                      value={maxSceneCount}
                      onChange={(event) => setMaxSceneCount(event.target.value)}
                    />
                  </Field>
                ) : (
                  <div className="border border-[#D5DAE0] bg-[#F7F8FA] p-2.5 text-[10px] leading-4 text-muted-foreground">
                    ASR은 시간창만 적용하며 scene count를 사용하지 않습니다.
                  </div>
                )}
              </div>
              <div className="mt-3 flex flex-wrap gap-2 font-mono text-[9px] text-muted-foreground">
                <span className="border border-[#D5DAE0] bg-[#F7F8FA] px-2 py-1">
                  preview cap duration=60s
                </span>
                {boundsCapability.hasSceneCount ? (
                  <span className="border border-[#D5DAE0] bg-[#F7F8FA] px-2 py-1">
                    preview cap scenes=12
                  </span>
                ) : null}
                <span className="border border-[#BCD9D6] bg-[#F4FAF9] px-2 py-1 text-[#246B62]">
                  requested ≠ applied이면 결과 evidence에 둘 다 표시
                </span>
              </div>
              {!isProcessingBoundsValid ? (
                <p className="mt-2 text-[10px] text-destructive">
                  duration은 0.001초 이상, scene count는 1 이상의 정수여야
                  합니다.
                </p>
              ) : null}
            </section>
          ) : null}

          <div className="flex justify-end border-t border-[#C5CBD3] pt-3">
            <Button
              className="rounded-[2px]"
              disabled={
                !label.trim() ||
                !isProcessorPinValid ||
                !parameters.value ||
                !isKnownPresetParameterValid ||
                !isProcessingBoundsValid
              }
              onClick={handleApply}
            >
              Apply configuration
            </Button>
          </div>
        </div>
      </div>
      <ProcessorPassport preset={selectedPreset} processorId={processorId} />
    </div>
  );
}

export function VisionEmbeddingBoard({
  node,
  activeTab,
  onApply,
}: MediaBoardProps) {
  const config = nodeDataOf(node);
  const [label, setLabel] = useState(
    asText(config.label) ?? "Vision embedding",
  );
  const [modelRef, setModelRef] = useState(
    asText(config.modelRef) ?? "clip-ViT-B-32",
  );

  if (activeTab === "inputs") {
    return (
      <ContractPane
        title="Visual media input"
        description="Committed media selection 또는 frame/image derivative를 받습니다. text embedding과 vector space를 섞지 않습니다."
      >
        <ContractFlow
          steps={[
            "media_set_selection | media_derivative_set",
            modelRef,
            "vision vector space",
          ]}
        />
        <ContractNote>
          scene-frame extraction 뒤에 연결하면 각 프레임을 같은 visual space로
          색인할 수 있습니다. 자연어 검색도 반드시 같은 model의 text tower를
          사용해야 하며 text BGE index와 합치면 안 됩니다.
        </ContractNote>
      </ContractPane>
    );
  }

  if (activeTab === "output") {
    return (
      <ContractPane
        title="Vision index generation contract"
        description="서버 descriptor의 출력 계약은 vector_index_generation이지만, 현재 no-commit executor는 이 노드를 실행하지 않습니다."
      >
        <pre className="overflow-auto border border-[#C5CBD3] bg-[#17212B] p-3 font-mono text-[10px] leading-5 text-[#D7E0EA]">
          {visionIndexSample(modelRef)}
        </pre>
        <ContractNote>
          위 payload는 작성·검증할 계약이며 현재 preview 결과라고 표시하지
          않습니다. Graph 저장과 named-port 검증은 가능하지만 serving index나
          preview vector는 생성되지 않습니다.
        </ContractNote>
      </ContractPane>
    );
  }

  return (
    <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_286px]">
      <div className="min-h-0 overflow-y-auto p-4">
        <div className="mx-auto max-w-3xl space-y-4">
          <BoardTitle
            icon={Eye}
            title="Vision embedding"
            description="이미지와 text query를 같은 contrastive vector space에 고정하는 Graph v2 node를 작성합니다."
          />
          <section className="border border-[#C5CBD3] bg-white p-3">
            <SectionHeading
              title="Current model contract"
              description="현재 로컬 vision adapter가 공개하는 model version입니다. 별도 model catalog가 연결되기 전에는 임의의 preset을 추가하지 않습니다."
            />
            <button
              type="button"
              aria-label="Use CLIP vision model preset"
              aria-pressed={modelRef === "clip-ViT-B-32"}
              className={cn(
                "mt-3 w-full border p-3 text-left",
                modelRef === "clip-ViT-B-32"
                  ? "border-[#7961DB] bg-[#F1EEFB]"
                  : "border-[#C5CBD3] hover:bg-[#F7F8FA]",
              )}
              onClick={() => setModelRef("clip-ViT-B-32")}
            >
              <div className="flex items-center gap-2">
                <Eye className="size-4 text-[#7961DB]" />
                <span className="text-[11px] font-semibold">
                  CLIP shared image/text space
                </span>
                <StatusPill intent="warning" className="ml-auto">
                  validation-only node
                </StatusPill>
              </div>
              <div className="mt-2 font-mono text-[10px] text-[#5846A5]">
                clip-ViT-B-32
              </div>
              <p className="mt-1 text-[9px] leading-4 text-muted-foreground">
                Image tower와 text-query tower가 같은 vector space를 사용합니다.
              </p>
            </button>
          </section>

          <section className="grid gap-3 border border-[#C5CBD3] bg-white p-3 md:grid-cols-2">
            <Field label="Node name">
              <Input
                aria-label="Vision embedding node name"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </Field>
            <Field label="Pinned vision model">
              <Input
                aria-label="Pinned vision embedding model"
                className="font-mono"
                value={modelRef}
                onChange={(event) => setModelRef(event.target.value)}
              />
            </Field>
          </section>

          <div className="flex justify-end border-t border-[#C5CBD3] pt-3">
            <Button
              className="rounded-[2px]"
              disabled={!label.trim() || !modelRef.trim()}
              onClick={() => onApply(node.id, { label, modelRef })}
            >
              Apply configuration
            </Button>
          </div>
        </div>
      </div>
      <aside className="border-l border-[#C5CBD3] bg-white p-3">
        <div className="text-[10px] font-bold tracking-[0.08em] text-muted-foreground uppercase">
          Runtime truth
        </div>
        <dl className="mt-3 space-y-2 text-[10px]">
          <SummaryRow label="model" value={modelRef} />
          <SummaryRow label="input" value="media | derivatives" />
          <SummaryRow label="output" value="vector index generation" />
          <SummaryRow label="preview" value="not executable yet" />
          <SummaryRow label="serving" value="not created" />
        </dl>
        <div className="mt-4 border border-[#E2C98B] bg-[#FFF8E7] p-2.5 text-[10px] leading-4 text-[#725B20]">
          이 노드는 캔버스 작성·저장·타입 검증까지 지원합니다. 현재
          no-commit preview executor와 deploy runtime에는 vision embedding
          handler가 없으므로 실행 가능하다고 표시하지 않습니다.
        </div>
      </aside>
    </div>
  );
}

function ProcessorPassport({
  preset,
  processorId,
}: {
  preset: ProcessorPreset | undefined;
  processorId: string;
}) {
  return (
    <aside className="border-l border-[#C5CBD3] bg-white p-3">
      <div className="text-[10px] font-bold tracking-[0.08em] text-muted-foreground uppercase">
        Processor passport
      </div>
      <dl className="mt-3 space-y-2 text-[10px]">
        <SummaryRow label="processor" value={processorId} />
        <SummaryRow label="profile" value={preset?.profile ?? "registry lookup"} />
        <SummaryRow label="model" value={preset?.model ?? "registry lookup"} />
        <SummaryRow label="output" value={preset?.outputKind ?? "registry lookup"} />
        <SummaryRow label="preview" value="bounded · no commit" />
      </dl>
      <div className="mt-4 border border-[#BCD9D6] bg-[#F4FAF9] p-2.5 text-[10px] leading-4">
        `transform.media` preview는 exact processor pin을 server registry에서 다시
        resolve합니다. 같은 bytes와 spec은 같은 derivative identity를 사용하고
        serving version은 만들지 않습니다.
      </div>
      <div className="mt-3 border border-[#E2C98B] bg-[#FFF8E7] p-2.5 text-[10px] leading-4 text-[#725B20]">
        캔버스 preset은 설치 성공을 보장하지 않습니다. 외부 binary나 model이
        없으면 preview run이 원인을 포함한 실패로 종료됩니다.
      </div>
    </aside>
  );
}

function ContractPane({
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
        <div key={`${step}-${index}`} className="contents">
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

function ContractNote({ children }: { children: React.ReactNode }) {
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
  icon: typeof Images;
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

function PresetIcon({ presetId }: { presetId: string }) {
  if (presetId === "image_ocr") {
    return <ScanText className="size-4 text-[#2D72D2]" />;
  }
  if (presetId === "audio_asr") {
    return <AudioLines className="size-4 text-[#2D72D2]" />;
  }
  if (presetId.startsWith("video") || presetId.startsWith("scene")) {
    return <Video className="size-4 text-[#2D72D2]" />;
  }
  return <Images className="size-4 text-[#2D72D2]" />;
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
    <div className="grid grid-cols-[68px_minmax(0,1fr)] gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="break-all font-mono">{value}</dd>
    </div>
  );
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

type ProcessorBoundsCapability = {
  hasDuration: boolean;
  hasSceneCount: boolean;
};

function processorBoundsCapability(
  processorId: string,
): ProcessorBoundsCapability {
  const processor = processorId.split("@", 1)[0];
  return {
    hasDuration: ["asr_v1", "video_frames_v1", "video_vision_v1"].includes(
      processor,
    ),
    hasSceneCount: ["video_frames_v1", "video_vision_v1"].includes(processor),
  };
}

function processingBoundsPatch(
  capability: ProcessorBoundsCapability,
  durationSeconds: number | null,
  sceneCount: number | null,
): Record<string, number> | undefined {
  if (!capability.hasDuration || durationSeconds === null) return undefined;
  const bounds: Record<string, number> = {
    maxDurationMs: Math.round(durationSeconds * 1000),
  };
  if (capability.hasSceneCount && sceneCount !== null) {
    bounds.maxSceneCount = sceneCount;
  }
  return bounds;
}

function positiveInteger(value: unknown): number {
  return typeof value === "number" &&
    Number.isInteger(value) &&
    value > 0
    ? value
    : 0;
}

function positiveNumber(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0.001 ? parsed : null;
}

function positiveIntegerText(value: string): number | null {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
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
      : { value: null, error: "parameters는 JSON object여야 합니다." };
  } catch {
    return { value: null, error: "유효한 JSON object를 입력하세요." };
  }
}

function mediaDerivativeSample(
  processorId: string,
  outputKind: string | undefined,
): string {
  return JSON.stringify(
    {
      artifactKind: "media_derivative_set",
      processorId,
      derivativeKind: outputKind ?? "resolved_by_registry",
      sourceMediaItemVersionId: "miv-...",
      processingSpecHash: "sha256:...",
      modelVersion: "pinned-by-registry",
      securityEnvelope: "inherited",
      serving: false,
    },
    null,
    2,
  );
}

function visionIndexSample(modelRef: string): string {
  return JSON.stringify(
    {
      artifactKind: "vector_index_generation",
      modality: "vision",
      modelRef,
      vectorSpace: "shared-image-text",
      serving: false,
      executionStatus: "contract-only",
    },
    null,
    2,
  );
}
