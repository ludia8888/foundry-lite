import type { PipelinePreviewRun } from "@foundry-lite/sdk";
import {
  Boxes,
  Cpu,
  Fingerprint,
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";
import { useMemo } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import {
  compactJson,
  previewArtifacts,
  previewPassport,
  recordList,
  stringList,
  textValue,
  type PreviewRecord,
} from "../pipeline-preview-model";

interface ArtifactPassportProps {
  run: PipelinePreviewRun;
  output: PreviewRecord | null;
  className?: string;
}

/** Preview artifact의 종류·버전 pin·보안 상속·비서빙 상태를 한곳에 묶은 증거 카드. */
export function ArtifactPassport({
  run,
  output,
  className,
}: ArtifactPassportProps) {
  const evidence = useMemo(() => passportEvidence(run, output), [output, run]);

  return (
    <aside
      className={cn(
        "h-full min-h-0 w-72 shrink-0 overflow-y-auto border-l border-[#A7CFCB] bg-[#F4FAF9]",
        className,
      )}
      aria-label="Artifact Passport"
    >
      <div className="sticky top-0 z-10 border-b border-[#C5E0DD] bg-[#E8F5F3] px-3 py-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-[#147D75]" />
          <span className="text-[12px] font-bold tracking-wide">
            Artifact Passport
          </span>
          <StatusPill intent="success" className="ml-auto">
            serving=false
          </StatusPill>
        </div>
        <p className="mt-1 text-[10px] leading-4 text-[#3E625E]">
          이 증거는 preview run에만 존재하며 serving 자산으로 승격되지 않습니다.
        </p>
      </div>

      <div className="space-y-3 p-3">
        <PassportSection icon={Boxes} title="Artifact">
          <PassportField label="종류" value={evidence.artifactKind} />
          <PassportField label="descriptor" value={evidence.descriptorId} />
          <PassportField label="spec version" value={evidence.specVersion} />
          <PassportField label="item count" value={evidence.itemCount} />
        </PassportSection>

        <PassportSection icon={Cpu} title="Processor / model pin">
          <EvidenceList
            values={evidence.processorPins}
            emptyLabel="processor pin 없음"
          />
          <EvidenceList
            values={evidence.modelPins}
            emptyLabel="model pin 없음"
          />
        </PassportSection>

        <PassportSection icon={LockKeyhole} title="Security inheritance">
          {evidence.securityEnvelopes.length === 0 ? (
            <p className="text-[10px] text-muted-foreground">
              별도 envelope가 반환되지 않았습니다.
            </p>
          ) : (
            <div className="space-y-1">
              {evidence.securityEnvelopes.map((envelope, index) => (
                <div
                  key={`${compactJson(envelope)}-${index}`}
                  className="rounded border border-[#BCD9D6] bg-white px-2 py-1 font-mono text-[10px]"
                >
                  {securityLabel(envelope)}
                </div>
              ))}
            </div>
          )}
        </PassportSection>

        <PassportSection icon={Fingerprint} title="Trace">
          <PassportField label="preview run" value={run.id} />
          <PassportField
            label="graph fp"
            value={shortFingerprint(textValue(run.graphFingerprint))}
          />
          <PassportField
            label="target"
            value={textValue(run.targetNodeId) ?? "-"}
          />
        </PassportSection>
      </div>
    </aside>
  );
}

function PassportSection({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof Boxes;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-1.5">
      <div className="flex items-center gap-1.5 text-[10px] font-bold tracking-[0.08em] text-[#376B66] uppercase">
        <Icon className="size-3.5" />
        {title}
      </div>
      <div className="space-y-1">{children}</div>
    </section>
  );
}

function PassportField({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="grid grid-cols-[78px_minmax(0,1fr)] gap-2 text-[10px]">
      <span className="text-muted-foreground">{label}</span>
      <span className="break-all font-mono text-foreground">{value}</span>
    </div>
  );
}

function EvidenceList({
  values,
  emptyLabel,
}: {
  values: readonly string[];
  emptyLabel: string;
}) {
  if (values.length === 0) {
    return <p className="text-[10px] text-muted-foreground">{emptyLabel}</p>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {values.map((value) => (
        <span
          key={value}
          className="rounded border border-[#BCD9D6] bg-white px-1.5 py-0.5 font-mono text-[10px]"
        >
          {value}
        </span>
      ))}
    </div>
  );
}

function passportEvidence(
  run: PipelinePreviewRun,
  output: PreviewRecord | null,
) {
  const artifacts = previewArtifacts(run);
  const outputPassport = previewPassport(output);
  const passports = [
    ...(outputPassport ? [outputPassport] : []),
    ...artifacts.flatMap((artifact) => {
      const passport = previewPassport(artifact);
      return passport ? [passport] : [];
    }),
  ];
  return {
    artifactKind:
      textValue(output?.artifactKind) ??
      textValue(outputPassport?.artifactKind) ??
      "-",
    descriptorId:
      textValue(outputPassport?.descriptorId) ??
      textValue(output?.descriptorId) ??
      "-",
    specVersion: String(outputPassport?.specVersion ?? output?.specVersion ?? "-"),
    itemCount: String(output?.itemCount ?? recordList(output?.items).length),
    processorPins: uniqueStrings(
      passports.flatMap((passport) => stringList(passport.processorPins)),
    ),
    modelPins: uniqueStrings(
      passports.flatMap((passport) =>
        recordList(passport.modelPins).map(modelPinLabel),
      ),
    ),
    securityEnvelopes: uniqueRecords(
      passports.flatMap((passport) =>
        recordList(passport.securityEnvelopes),
      ),
    ),
  };
}

function modelPinLabel(model: PreviewRecord): string {
  const name =
    textValue(model.name) ??
    textValue(model.model) ??
    textValue(model.modelName) ??
    "model";
  const version =
    textValue(model.version) ??
    textValue(model.modelVersion) ??
    textValue(model.ref);
  return version ? `${name}@${version}` : name;
}

function securityLabel(envelope: PreviewRecord): string {
  const classification =
    textValue(envelope.classification) ??
    textValue(envelope.securityClassification) ??
    "inherited";
  const tenant = textValue(envelope.tenantId) ?? textValue(envelope.tenant_id);
  return tenant ? `${classification} · tenant=${tenant}` : classification;
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function uniqueRecords(values: readonly PreviewRecord[]): PreviewRecord[] {
  const byJson = new Map(values.map((value) => [JSON.stringify(value), value]));
  return [...byJson.values()];
}

function shortFingerprint(value: string | null): string {
  if (!value) return "-";
  return value.length > 18 ? `${value.slice(0, 18)}…` : value;
}
