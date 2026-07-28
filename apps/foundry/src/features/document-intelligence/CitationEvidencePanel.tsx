import {
  FileSearch,
  LoaderCircle,
  LockKeyhole,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";

import type { CitationNavigationResolution } from "./citation-evidence-model";

interface CitationEvidencePanelProps {
  resolution: CitationNavigationResolution | null;
  isLoading: boolean;
  hasError: boolean;
}

export function CitationEvidencePanel({
  resolution,
  isLoading,
  hasError,
}: CitationEvidencePanelProps) {
  const evidence = resolution?.evidence ?? null;
  const status = citationEvidenceStatus(resolution, hasError);
  return (
    <aside
      className="flex w-[390px] shrink-0 flex-col border-l border-[#C8CED6] bg-white"
      data-testid="citation-evidence-panel"
    >
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-[#D3D8DE] px-3">
        <div className="flex items-center gap-2">
          {status === "blocked" ? (
            <ShieldAlert className="size-4 text-[#C23030]" />
          ) : status === "verifying" ? (
            <LoaderCircle className="size-4 animate-spin text-[#137CBD]" />
          ) : (
            <ShieldCheck className="size-4 text-[#0F9960]" />
          )}
          <div>
            <h2 className="text-[12px] font-semibold">Verified evidence</h2>
            <p className="text-[9px] text-[#738091]">
              signed citation · authoritative reread
            </p>
          </div>
        </div>
        <span
          className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${
            status === "committed"
              ? "bg-[#E7F6EC] text-[#0F6B3E]"
              : status === "blocked"
                ? "bg-[#FDECEC] text-[#A82A2A]"
                : "bg-[#E1F3F8] text-[#0E5A78]"
          }`}
          data-testid="citation-evidence-status"
        >
          {status}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {isLoading ? (
          <PanelState
            icon={<LoaderCircle className="size-5 animate-spin" />}
            title="서버에서 근거를 다시 검증 중입니다"
            detail="검증이 끝나기 전에는 원문과 좌표를 표시하지 않습니다."
          />
        ) : hasError || !resolution || !evidence ? (
          <PanelState
            icon={<ShieldAlert className="size-5" />}
            title="검증된 근거를 표시할 수 없습니다"
            detail="권한, 서명, 원본 버전 또는 content hash 검증에 실패했습니다."
          />
        ) : (
          <div className="space-y-3" data-testid="verified-citation-passport">
            <EvidenceSection title="Source identity">
              <EvidenceRow label="Label" value={resolution.displayLabel} />
              <EvidenceRow
                label="Resource"
                value={`${resolution.sourceResourceType}:${resolution.sourceResourceId}`}
                mono
              />
              <EvidenceRow
                label="Source version"
                value={resolution.sourceVersion}
                mono
              />
              <EvidenceRow
                label="Content hash"
                value={resolution.contentHash}
                mono
              />
            </EvidenceSection>

            <EvidenceSection title="Exact document location">
              <EvidenceRow
                label="Media version"
                value={evidence.mediaItemVersionId}
                mono
              />
              <EvidenceRow
                label="Derivative"
                value={evidence.mediaDerivativeId}
                mono
              />
              <EvidenceRow
                label="Content unit"
                value={evidence.contentUnitId}
                mono
              />
              <EvidenceRow
                label="Page"
                value={String(evidence.pageNumber)}
              />
              <JsonEvidence label="Bounding box" value={evidence.bbox} />
              <JsonEvidence
                label="Source locator"
                value={evidence.sourceLocator}
              />
            </EvidenceSection>

            <EvidenceSection title="Pinned processing">
              <EvidenceRow
                label="Derivative kind"
                value={evidence.derivativeKind}
              />
              <EvidenceRow
                label="Processor"
                value={`${evidence.processorName}@${evidence.processorVersion}`}
                mono
              />
              <EvidenceRow
                label="Processor spec"
                value={evidence.processorSpecHash}
                mono
              />
              <EvidenceRow
                label="Model"
                value={modelPin(evidence.modelName, evidence.modelVersion)}
                mono
              />
              <EvidenceRow
                label="Parameters"
                value={evidence.paramsHash}
                mono
              />
            </EvidenceSection>

            <EvidenceSection title="Security">
              <JsonEvidence
                label="Inherited envelope"
                value={evidence.securityEnvelope}
              />
            </EvidenceSection>

            <div className="rounded-[2px] border border-[#E2C36E] bg-[#FFF8E5] p-2 text-[9px] leading-4 text-[#775500]">
              <div className="mb-1 flex items-center gap-1 font-semibold">
                <LockKeyhole className="size-3" />
                좌표 신뢰 경계
              </div>
              URL에는 signed citation 식별자만 사용합니다. page와 bbox는 서버가
              immutable Content Unit을 다시 읽은 결과만 표시합니다. 브라우저 기본
              PDF 뷰어는 페이지 여백을 적용할 수 있어, 시각 overlay는 좌표 근거를
              보조하는 표시입니다.
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

function citationEvidenceStatus(
  resolution: CitationNavigationResolution | null,
  hasError: boolean,
): "verifying" | "committed" | "blocked" {
  if (hasError) return "blocked";
  if (resolution) return "committed";
  return "verifying";
}

function PanelState({
  icon,
  title,
  detail,
}: {
  icon: React.ReactNode;
  title: string;
  detail: string;
}) {
  return (
    <div className="flex h-full min-h-48 flex-col items-center justify-center gap-2 text-center text-[#738091]">
      {icon}
      <div className="text-[11px] font-semibold text-[#303742]">{title}</div>
      <p className="max-w-64 text-[9px] leading-4">{detail}</p>
    </div>
  );
}

function EvidenceSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-[2px] border border-[#D3D8DE]">
      <div className="flex h-7 items-center gap-1.5 border-b border-[#E5E8EB] bg-[#F6F7F9] px-2 text-[9px] font-semibold tracking-[0.4px] text-[#5F6B7C] uppercase">
        <FileSearch className="size-3" />
        {title}
      </div>
      <div className="space-y-1.5 p-2">{children}</div>
    </section>
  );
}

function EvidenceRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="grid grid-cols-[104px_minmax(0,1fr)] items-start gap-2 text-[9px]">
      <span className="text-[#738091]">{label}</span>
      <span
        className={`break-all text-[#303742] ${mono ? "font-mono" : ""}`}
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

function JsonEvidence({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <div className="mb-1 text-[8px] font-medium text-[#738091]">{label}</div>
      <pre className="max-h-28 overflow-auto rounded-[2px] bg-[#111820] p-2 font-mono text-[8px] leading-3 text-[#D8E1EA]">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function modelPin(name: string | null, version: string | null): string {
  if (name && version) return `${name}@${version}`;
  return name ?? version ?? "not used";
}
