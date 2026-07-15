import { ArrowLeft, ChevronRight, Database, DatabaseZap } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { RequestTelemetry } from "@/components/shared/RequestTelemetry";
import { Button } from "@/components/ui/button";

import { useSourceTemplates } from "../use-source-queries";
import { BatchFileFlow } from "./BatchFileFlow";
import { CompletionStep } from "./CompletionStep";
import type { ConnectionMode } from "./ConnectionMethodStep";
import { ConnectionMethodStep } from "./ConnectionMethodStep";
import type { WizardCompletion } from "./CsvUploadFlow";
import { CsvUploadFlow } from "./CsvUploadFlow";
import { DebeziumCdcFlow } from "./DebeziumCdcFlow";
import { ManagedSourceFlow } from "./ManagedSourceFlow";
import { MediaUploadFlow } from "./MediaUploadFlow";
import { SaveToProjectStep } from "./SaveToProjectStep";
import type { WizardTemplate } from "./TemplatePickerStep";
import {
  buildWizardTemplates,
  TemplatePickerStep,
} from "./TemplatePickerStep";
import { WebhookListenerFlow } from "./WebhookListenerFlow";
import {
  MANAGED_SOURCE_STEPS,
  NEW_SOURCE_STEPS,
} from "./source-wizard-steps";
import { WizardStepLayout } from "./WizardStepLayout";
import type { WizardStepMeta } from "./WizardStepLayout";

type WizardPhase = "type" | "connection" | "project" | "configure" | "done";

interface SourceWizardProps {
  initialSourceType?: string | null;
  onCancel: () => void;
  onComplete: (completion: WizardCompletion) => void;
}

/**
 * 새 소스 풀스크린 flow (Palantir set-up-source 6단계 구조):
 * 소스 유형 선택 → 연결 방식(에이전트/직접) → 프로젝트에 저장 →
 * 타입별 구성+자격 증명 flow → 완료(evidence + 이동 액션).
 */
export function SourceWizard({
  initialSourceType = null,
  onCancel,
  onComplete,
}: SourceWizardProps) {
  const templatesQuery = useSourceTemplates();
  const [phase, setPhase] = useState<WizardPhase>("type");
  const [template, setTemplate] = useState<WizardTemplate | null>(null);
  const autoSelectedSourceType = useRef<string | null>(null);
  const [connectionMode, setConnectionMode] =
    useState<ConnectionMode>("direct");
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState<string | null>(null);
  const [completion, setCompletion] = useState<WizardCompletion | null>(null);
  const wizardSteps =
    template?.flow === "managed" ? MANAGED_SOURCE_STEPS : NEW_SOURCE_STEPS;

  const handleSelectTemplate = useCallback((nextTemplate: WizardTemplate) => {
    setTemplate(nextTemplate);
    setConnectionMode("direct");
    setSelectedAgentId(null);
    setPhase("connection");
  }, []);

  useEffect(() => {
    if (
      !initialSourceType ||
      phase !== "type" ||
      template !== null ||
      autoSelectedSourceType.current === initialSourceType
    ) {
      return;
    }

    const nextTemplate = buildWizardTemplates(templatesQuery.data ?? []).find(
      (candidate) => candidate.sourceType === initialSourceType,
    );
    if (!nextTemplate || nextTemplate.flow === "future") return;

    autoSelectedSourceType.current = initialSourceType;
    handleSelectTemplate(nextTemplate);
  }, [
    handleSelectTemplate,
    initialSourceType,
    phase,
    template,
    templatesQuery.data,
  ]);

  const handleFlowComplete = (flowCompletion: WizardCompletion) => {
    setCompletion(flowCompletion);
    setPhase("done");
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Database className="size-3.5" />
          <span>Data Connection</span>
          <ChevronRight className="size-3" />
          <span className="font-medium text-foreground">새 소스</span>
        </div>
        <RequestTelemetry />
      </div>
      {phase === "type" ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-3 border-b px-4 py-2">
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={onCancel}
              aria-label="목록으로 돌아가기"
            >
              <ArrowLeft className="size-4" />
            </Button>
            <span className="flex size-9 items-center justify-center rounded bg-primary/10">
              <DatabaseZap className="size-4 text-primary" />
            </span>
            <div>
              <div className="text-[13px] font-semibold">제목 없는 소스</div>
              <div className="text-[11px] text-muted-foreground">
                소스 유형을 선택하세요
              </div>
            </div>
            <div className="ml-auto">
              <Button variant="outline" size="sm" onClick={onCancel}>
                취소
              </Button>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-6">
            <TemplatePickerStep
              templates={templatesQuery.data}
              isLoading={templatesQuery.isLoading}
              error={templatesQuery.error}
              onRetry={() => void templatesQuery.reload()}
              onSelect={handleSelectTemplate}
            />
          </div>
        </div>
      ) : null}
      {template !== null && phase === "connection" ? (
        <WizardStepLayout
          title={displayName.trim() || "제목 없는 소스"}
          subtitle={`${template.displayName} · 연결 방식`}
          steps={wizardSteps}
          activeIndex={1}
          onBack={() => setPhase("type")}
          onCancel={onCancel}
        >
          <ConnectionMethodStep
            templateDisplayName={template.displayName}
            supportsAgent={template.networkModes.includes("agent_proxy")}
            connectionMode={connectionMode}
            onConnectionModeChange={(mode) => {
              setConnectionMode(mode);
              if (mode === "direct") setSelectedAgentId(null);
            }}
            selectedAgentId={selectedAgentId}
            onSelectedAgentIdChange={setSelectedAgentId}
            onContinue={() => setPhase("project")}
          />
        </WizardStepLayout>
      ) : null}
      {template !== null && phase === "project" ? (
        <WizardStepLayout
          title={displayName.trim() || "제목 없는 소스"}
          subtitle={`${template.displayName} · 프로젝트에 저장`}
          steps={wizardSteps}
          activeIndex={2}
          onBack={() => setPhase("connection")}
          onCancel={onCancel}
        >
          <SaveToProjectStep
            templateDisplayName={template.displayName}
            displayName={displayName}
            onDisplayNameChange={setDisplayName}
            projectId={projectId}
            onProjectIdChange={setProjectId}
            onProjectNameChange={setProjectName}
            onContinue={() => setPhase("configure")}
          />
        </WizardStepLayout>
      ) : null}
      {template !== null && phase === "configure" ? (
        <ConfigureFlow
          template={template}
          displayName={displayName}
          connectionMode={connectionMode}
          agentId={selectedAgentId}
          wizardSteps={wizardSteps}
          onExit={() => setPhase("project")}
          onCancel={onCancel}
          onComplete={handleFlowComplete}
        />
      ) : null}
      {template !== null && completion !== null && phase === "done" ? (
        <WizardStepLayout
          title={displayName.trim() || "제목 없는 소스"}
          subtitle={`${template.displayName} · 완료`}
          steps={wizardSteps}
          activeIndex={wizardSteps.length - 1}
          onCancel={onCancel}
        >
          <CompletionStep
            completion={completion}
            templateDisplayName={template.displayName}
            connectionMode={connectionMode}
            agentId={selectedAgentId}
            projectName={projectName}
            onGoToSyncDetail={() => onComplete(completion)}
            onGoToSourceExplore={() =>
              onComplete({ sourceName: completion.sourceName, syncName: null })
            }
          />
        </WizardStepLayout>
      ) : null}
    </div>
  );
}

/** 4단계 구성+자격 증명: 타입별 기존 flow 컴포넌트를 새 단계 프레임에 연결. */
function ConfigureFlow({
  template,
  displayName,
  connectionMode,
  agentId,
  wizardSteps,
  onExit,
  onCancel,
  onComplete,
}: {
  template: WizardTemplate;
  displayName: string;
  connectionMode: ConnectionMode;
  agentId: string | null;
  wizardSteps: readonly WizardStepMeta[];
  onExit: () => void;
  onCancel: () => void;
  onComplete: (completion: WizardCompletion) => void;
}) {
  const flowProps = {
    initialDisplayName: displayName,
    onExit,
    onCancel,
    onComplete,
  };
  if (template.flow === "csv") return <CsvUploadFlow {...flowProps} />;
  if (template.flow === "batch_file") return <BatchFileFlow {...flowProps} />;
  if (template.flow === "webhook_listener")
    return <WebhookListenerFlow {...flowProps} />;
  if (template.flow === "debezium_cdc")
    return <DebeziumCdcFlow {...flowProps} />;
  if (template.flow === "media_upload")
    return <MediaUploadFlow {...flowProps} />;
  return (
    <ManagedSourceFlow
      template={template}
      connectionMode={connectionMode}
      agentId={agentId}
      wizardSteps={wizardSteps}
      {...flowProps}
    />
  );
}
