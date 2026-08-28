import { Activity, Radio } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { EditorCard } from "./NewSyncEditorSections";
import type {
  KafkaPartitionMode,
  KafkaUpstreamMode,
  NewSyncDraft,
  NewSyncDraftUpdater,
} from "./new-sync-model";

interface DraftSectionProps {
  draft: NewSyncDraft;
  updateDraft: NewSyncDraftUpdater;
}
export function KafkaSourceConfiguration({
  draft,
  updateDraft,
}: DraftSectionProps) {
  return (
    <EditorCard icon={Radio} title="소스별 구성 — Kafka topic">
      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-1 md:col-span-2">
          <Label className="text-[11px]">Topic</Label>
          <Input
            value={draft.kafkaTopic}
            onChange={(event) => {
              const topic = event.target.value;
              updateDraft("kafkaTopic", topic);
              if (!draft.kafkaStreamName) {
                updateDraft(
                  "kafkaStreamName",
                  topic.replace(/[^a-zA-Z0-9_-]/g, "-"),
                );
              }
            }}
            placeholder="crypto.trades"
            className="h-7 font-mono text-xs"
          />
        </div>
        <TextDraftField
          label="Consumer group"
          value={draft.kafkaConsumerGroup}
          onChange={(value) => updateDraft("kafkaConsumerGroup", value)}
        />
        <TextDraftField
          label="Stream name"
          value={draft.kafkaStreamName}
          onChange={(value) => updateDraft("kafkaStreamName", value)}
        />
        <div className="space-y-1">
          <Label className="text-[11px]">Partition strategy</Label>
          <Select
            value={draft.kafkaPartitionMode}
            onValueChange={(value) =>
              updateDraft(
                "kafkaPartitionMode",
                value as KafkaPartitionMode,
              )
            }
          >
            <SelectTrigger size="sm" className="w-full text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All partitions · 자동 발견</SelectItem>
              <SelectItem value="single">Single partition</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <TextDraftField
          label="Micro-batch records"
          value={draft.kafkaBatchLimitText}
          inputMode="numeric"
          onChange={(value) => updateDraft("kafkaBatchLimitText", value)}
        />
        {draft.kafkaPartitionMode === "single" ? (
          <div className="space-y-1 md:col-span-2">
            <Label className="text-[11px]">Partition number</Label>
            <Input
              value={draft.kafkaPartitionText}
              onChange={(event) =>
                updateDraft("kafkaPartitionText", event.target.value)
              }
              inputMode="numeric"
              className="h-7 font-mono text-xs"
            />
          </div>
        ) : (
          <div className="rounded border border-primary/20 bg-primary/5 px-2.5 py-2 text-[10px] text-muted-foreground md:col-span-2">
            실행할 때 topic metadata를 다시 읽고 모든 partition을 독립
            offset으로 체크포인트합니다. topic partition이 늘어나도 다음
            micro-batch부터 자동 반영됩니다.
          </div>
        )}
        <div className="space-y-1 md:col-span-2">
          <Label className="text-[11px]">Upstream producer</Label>
          <Select
            value={draft.kafkaUpstreamMode}
            onValueChange={(value) =>
              updateDraft("kafkaUpstreamMode", value as KafkaUpstreamMode)
            }
          >
            <SelectTrigger size="sm" className="w-full text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="external">외부 Kafka producer</SelectItem>
              <SelectItem value="kraken">
                Kraken WebSocket v2 · live trades
              </SelectItem>
            </SelectContent>
          </Select>
          <div className="text-[10px] text-muted-foreground">
            Kraken을 선택하면 상시 worker가 공개 trade feed를 이 topic에
            publish한 뒤 같은 checkpoint 경로로 Dataset에 보관합니다.
          </div>
        </div>
        {draft.kafkaUpstreamMode === "kraken" ? (
          <div className="space-y-1 md:col-span-2">
            <Label className="text-[11px]">Kraken symbol</Label>
            <Input
              value={draft.krakenSymbol}
              onChange={(event) =>
                updateDraft("krakenSymbol", event.target.value)
              }
              placeholder="BTC/USD"
              className="h-7 font-mono text-xs"
            />
            <div className="font-mono text-[10px] text-muted-foreground">
              wss://ws.kraken.com/v2 · channel=trade · snapshot=false
            </div>
          </div>
        ) : null}
      </div>
    </EditorCard>
  );
}

export function KafkaMonitoringConfiguration({
  draft,
  updateDraft,
}: DraftSectionProps) {
  return (
    <EditorCard icon={Activity} title="Production monitoring">
      <div className="grid gap-3 md:grid-cols-3">
        <TextDraftField
          label="Checkpoint liveness · sec"
          value={draft.checkpointLivenessText}
          inputMode="numeric"
          onChange={(value) => updateDraft("checkpointLivenessText", value)}
        />
        <TextDraftField
          label="Max checkpoint · ms"
          value={draft.maxCheckpointDurationText}
          inputMode="numeric"
          onChange={(value) =>
            updateDraft("maxCheckpointDurationText", value)
          }
        />
        <TextDraftField
          label="Max total lag · records"
          value={draft.maxBrokerLagText}
          inputMode="numeric"
          onChange={(value) => updateDraft("maxBrokerLagText", value)}
        />
      </div>
      <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
        Worker heartbeat, checkpoint liveness, duration, total lag,
        throughput를 매 상태 조회마다 판정합니다.
      </p>
    </EditorCard>
  );
}

function TextDraftField({
  label,
  value,
  inputMode,
  onChange,
}: {
  label: string;
  value: string;
  inputMode?: "text" | "numeric";
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-[11px]">{label}</Label>
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        inputMode={inputMode}
        className="h-7 font-mono text-xs"
      />
    </div>
  );
}
