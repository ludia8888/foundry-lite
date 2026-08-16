import type {
  SourceConnection,
  SourceExploreRequest,
  SourceExploreResult,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import {
  Activity,
  Cable,
  Database,
  Loader2,
  Radio,
  RefreshCw,
} from "lucide-react";
import { useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { readNumberField, readTextField } from "../source-model";
import { SourceExplorationEvidenceLink } from "./SourceExplorationEvidenceLink";

interface KafkaSourceExplorerProps {
  source: SourceConnection;
  onCreateSync?: (topicName: string) => void;
}

interface KafkaTopicView {
  topicName: string;
  partitionCount: number;
  isInternal: boolean;
}

/** Kafka Source → topic → micro-batch → dataset 흐름을 한 화면에서 검증한다. */
export function KafkaSourceExplorer({ source, onCreateSync }: KafkaSourceExplorerProps) {
  const client = useFoundryLiteClient();
  const [selectedTopic, setSelectedTopic] = useState(
    () => readTextField(source.configSummary, "topic") ?? "",
  );
  const [sampleLimitText, setSampleLimitText] = useState("20");
  const topicsMutation = useFoundryLiteMutation<
    SourceExploreResult,
    SourceExploreRequest
  >((payload) => client.sources.exploration.run(payload));
  const previewMutation = useFoundryLiteMutation<
    SourceExploreResult,
    SourceExploreRequest
  >((payload) => client.sources.exploration.run(payload));
  const topics = useMemo(
    () => readKafkaTopics(topicsMutation.result?.resultSummary),
    [topicsMutation.result],
  );
  const rows = readKafkaRows(previewMutation.result?.resultSummary);
  const checkpoint = readKafkaCheckpoint(previewMutation.result?.resultSummary);
  const sampleLimit = Number.parseInt(sampleLimitText, 10) || 20;

  const loadTopics = () =>
    topicsMutation.execute({
      sourceName: source.sourceName,
      sourceType: "kafka",
      request: { sampleLimit: 100 },
    });
  const previewTopic = (topicName: string) => {
    setSelectedTopic(topicName);
    return previewMutation.execute({
      sourceName: source.sourceName,
      sourceType: "kafka",
      request: {
        topic: topicName,
        streamName:
          readTextField(source.configSummary, "streamName") ??
          topicName.replace(/[^a-zA-Z0-9_-]/g, "-"),
        consumerGroup:
          readTextField(source.configSummary, "consumerGroup") ??
          `foundry-lite-preview-${source.sourceName}`,
        partition: readNumberField(source.configSummary, "partition") ?? 0,
        sampleLimit,
      },
    });
  };

  return (
    <div className="space-y-3">
      <StreamingRail
        sourceName={source.sourceName}
        topic={selectedTopic}
        targetDatasetRef={source.targetDatasetRef}
        hasPreview={rows.length > 0}
      />
      <div className="flex flex-wrap items-end justify-between gap-3 rounded border bg-card p-3">
        <div>
          <div className="text-[13px] font-semibold">Kafka topic explorer</div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            토픽 메타데이터와 원본 key/value를 읽습니다. 미리보기만으로는
            데이터셋 버전이나 소비 체크포인트가 생성되지 않습니다.
          </p>
        </div>
        <div className="flex items-end gap-2">
          <label className="space-y-1 text-[11px] text-muted-foreground">
            <span className="block">샘플 레코드</span>
            <Input
              value={sampleLimitText}
              onChange={(event) => setSampleLimitText(event.target.value)}
              className="h-8 w-24 font-mono text-xs"
              inputMode="numeric"
            />
          </label>
          <Button
            size="sm"
            variant="outline"
            disabled={topicsMutation.isRunning}
            onClick={() => void loadTopics()}
          >
            {topicsMutation.isRunning ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <RefreshCw className="size-3.5" />
            )}
            토픽 불러오기
          </Button>
        </div>
      </div>
      {topicsMutation.error ? (
        <div className="space-y-2">
          <ErrorState error={topicsMutation.error} />
          <SourceExplorationEvidenceLink error={topicsMutation.error} label="실패 실행 조사" />
        </div>
      ) : null}
      {previewMutation.error ? (
        <div className="space-y-2">
          <ErrorState error={previewMutation.error} />
          <SourceExplorationEvidenceLink error={previewMutation.error} label="실패 실행 조사" />
        </div>
      ) : null}
      <div className="grid min-h-[480px] overflow-hidden rounded border bg-card lg:grid-cols-[280px_1fr]">
        <aside className="border-r">
          <div className="section-label border-b px-3 py-2">Topics</div>
          {topics.length === 0 ? (
            <div className="p-4 text-xs text-muted-foreground">
              토픽 불러오기를 눌러 브로커의 리소스를 확인하세요.
            </div>
          ) : (
            <div className="divide-y">
              {topics.map((topic) => (
                <button
                  key={topic.topicName}
                  type="button"
                  onClick={() => void previewTopic(topic.topicName)}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-muted/50",
                    selectedTopic === topic.topicName && "bg-accent",
                    topic.isInternal && "opacity-60",
                  )}
                >
                  <Radio className="size-3.5 shrink-0 text-primary" />
                  <span className="min-w-0 flex-1 truncate font-mono text-xs">
                    {topic.topicName}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    {topic.partitionCount}p
                  </span>
                </button>
              ))}
            </div>
          )}
        </aside>
        <section className="min-w-0">
          <div className="flex items-center justify-between border-b px-3 py-2">
            <div className="min-w-0">
              <div className="truncate font-mono text-xs font-semibold">
                {selectedTopic || "토픽을 선택하세요"}
              </div>
              <div className="text-[10px] text-muted-foreground">
                preview evidence · checkpoint not committed
              </div>
            </div>
            <div className="flex items-center gap-2">
              <SourceExplorationEvidenceLink
                result={previewMutation.result ?? topicsMutation.result}
                className="text-[10px]"
              />
              <StatusPill intent="neutral">미리보기 전용</StatusPill>
              {checkpoint !== null ? (
                <span className="font-mono text-[10px] text-muted-foreground">
                  sampled offset {checkpoint}
                </span>
              ) : null}
              {selectedTopic && onCreateSync ? (
                <Button size="sm" onClick={() => onCreateSync(selectedTopic)}>
                  이 토픽으로 Sync 생성
                </Button>
              ) : null}
            </div>
          </div>
          <KafkaPreviewTable rows={rows} isLoading={previewMutation.isRunning} />
        </section>
      </div>
    </div>
  );
}

function StreamingRail({
  sourceName,
  topic,
  targetDatasetRef,
  hasPreview,
}: {
  sourceName: string;
  topic: string;
  targetDatasetRef: string | null;
  hasPreview: boolean;
}) {
  const nodes = [
    { icon: Cable, label: "Source", value: sourceName, isActive: true },
    { icon: Radio, label: "Topic", value: topic || "선택 대기", isActive: Boolean(topic) },
    { icon: Activity, label: "Micro-batch", value: hasPreview ? "샘플 수신" : "대기", isActive: hasPreview },
    { icon: Database, label: "Archive", value: targetDatasetRef ?? "Sync에서 지정", isActive: false },
  ];
  return (
    <div className="grid overflow-hidden rounded border bg-[#F6F8FA] md:grid-cols-4">
      {nodes.map(({ icon: Icon, label, value, isActive }, index) => (
        <div key={label} className="relative flex min-w-0 items-center gap-2 border-b px-3 py-2.5 md:border-r md:border-b-0">
          {index > 0 ? <span className="absolute top-1/2 -left-1 hidden size-2 -translate-y-1/2 rotate-45 border-t border-r bg-[#F6F8FA] md:block" /> : null}
          <span className={cn("flex size-7 items-center justify-center rounded border bg-white", isActive && "border-primary/50 text-primary")}>
            <Icon className="size-3.5" />
          </span>
          <span className="min-w-0">
            <span className="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
            <span className="block truncate font-mono text-[11px]">{value}</span>
          </span>
          {isActive ? <span className="ml-auto size-1.5 shrink-0 rounded-full bg-success shadow-[0_0_0_3px_rgba(15,153,96,0.12)]" /> : null}
        </div>
      ))}
    </div>
  );
}

function KafkaPreviewTable({ rows, isLoading }: { rows: Record<string, unknown>[]; isLoading: boolean }) {
  if (isLoading) return <div className="flex h-48 items-center justify-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-4 animate-spin" />메시지를 읽는 중...</div>;
  if (rows.length === 0) return <div className="flex h-48 items-center justify-center text-xs text-muted-foreground">선택한 토픽에서 아직 샘플 레코드를 읽지 않았습니다.</div>;
  return (
    <div className="overflow-auto">
      <table className="w-full min-w-[720px] border-collapse text-left text-xs">
        <thead className="sticky top-0 bg-muted/70 text-[10px] uppercase tracking-wide text-muted-foreground">
          <tr><th className="border-b px-3 py-2">offset</th><th className="border-b px-3 py-2">partition</th><th className="border-b px-3 py-2">key</th><th className="border-b px-3 py-2">value</th></tr>
        </thead>
        <tbody>{rows.map((row) => <tr key={`${String(row.partition)}:${String(row.offset)}`} className="border-b last:border-b-0"><td className="px-3 py-2 font-mono">{String(row.offset ?? "—")}</td><td className="px-3 py-2 font-mono">{String(row.partition ?? "—")}</td><td className="px-3 py-2 font-mono">{String(row.key ?? "—")}</td><td className="max-w-xl px-3 py-2 font-mono text-[11px]">{formatKafkaValue(row.value)}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function readKafkaTopics(summary: Record<string, unknown> | undefined): KafkaTopicView[] {
  const topics = summary?.topics;
  if (!Array.isArray(topics)) return [];
  return topics.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    if (typeof row.topicName !== "string" || typeof row.partitionCount !== "number") return [];
    return [{ topicName: row.topicName, partitionCount: row.partitionCount, isInternal: row.isInternal === true }];
  });
}

function readKafkaRows(summary: Record<string, unknown> | undefined): Record<string, unknown>[] {
  return Array.isArray(summary?.sampleRows) ? summary.sampleRows.filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === "object") : [];
}

function readKafkaCheckpoint(summary: Record<string, unknown> | undefined): number | null {
  const value = summary?.checkpoint;
  return value && typeof value === "object" ? readNumberField(value as Record<string, unknown>, "offset") : null;
}

function formatKafkaValue(value: unknown): string {
  if (typeof value === "string") return value;
  try { return JSON.stringify(value); } catch { return String(value); }
}
