import type {
  OntologyApplyRequest,
  OntologyRollbackRequest,
  OntologyValidateRequest,
} from "@foundry-lite/sdk";
import type {
  FoundryLiteOntologyBranchMutationsState,
  FoundryLiteOntologyBranchState,
} from "@foundry-lite/sdk/react";
import {
  foundryLiteOntologyDraftValidationView,
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { ChevronDown, FileCode2, Save, ShieldCheck, Undo2 } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { truncateMiddle } from "../lib/ontology-view";
import { ValidationResults } from "./ValidationResults";

interface DraftPanelProps {
  yamlText: string;
  isDraftDirty: boolean;
  branchDetail: FoundryLiteOntologyBranchState;
  updateMutation: FoundryLiteOntologyBranchMutationsState["update"];
  activeVersionNumber: number | null;
  onYamlChange: (text: string) => void;
  onSaveToBranch: (yamlText: string) => void;
  onOntologyChanged: () => void;
}

/**
 * 접이식 하단 패널: YAML 드래프트 편집기 + 검증 + 적용/롤백 컨트롤.
 * 검증을 통과하지 못한 텍스트는 적용 버튼이 열리지 않는다 (acceptance).
 */
export function DraftPanel({
  yamlText,
  isDraftDirty,
  branchDetail,
  updateMutation,
  activeVersionNumber,
  onYamlChange,
  onSaveToBranch,
  onOntologyChanged,
}: DraftPanelProps) {
  const client = useFoundryLiteClient();
  const [isOpen, setIsOpen] = useState(false);
  const [validatedText, setValidatedText] = useState<string | null>(null);
  const [rollbackTarget, setRollbackTarget] = useState("");

  const validateMutation = useFoundryLiteMutation(
    (payload: OntologyValidateRequest) => client.ontology.validate(payload),
    { lockKey: () => "ontology:validate" },
  );
  const applyMutation = useFoundryLiteMutation(
    (payload: OntologyApplyRequest) => client.ontology.apply(payload),
    { lockKey: () => "ontology:apply", onSuccess: onOntologyChanged },
  );
  const rollbackMutation = useFoundryLiteMutation(
    (payload: OntologyRollbackRequest) => client.ontology.rollback(payload),
    { lockKey: () => "ontology:rollback", onSuccess: onOntologyChanged },
  );

  const validationView = foundryLiteOntologyDraftValidationView(
    validateMutation.result,
  );
  const isValidatedCurrent =
    validateMutation.result !== null && validatedText === yamlText;
  const canApply =
    isValidatedCurrent && !validationView.isBlocked && !applyMutation.isRunning;

  const handleValidate = () => {
    void validateMutation.execute({ yaml: yamlText }).then((result) => {
      if (result) setValidatedText(yamlText);
    });
  };

  const handleRollback = () => {
    const versionNumber = Number.parseInt(rollbackTarget, 10);
    if (Number.isNaN(versionNumber)) return;
    void rollbackMutation.execute({ versionNumber });
  };

  const branch = branchDetail.branch;
  const canSaveToBranch =
    branch !== null && branch.status === "open" && isDraftDirty;

  return (
    <Collapsible
      open={isOpen}
      onOpenChange={setIsOpen}
      className="rounded border bg-card"
      id="ontology-draft-panel"
    >
      <CollapsibleTrigger className="flex h-9 w-full items-center gap-2 px-3 text-left">
        <ChevronDown
          className={cn(
            "size-3.5 text-muted-foreground transition-transform",
            !isOpen && "-rotate-90",
          )}
        />
        <FileCode2 className="size-3.5 text-primary" />
        <span className="text-xs font-semibold">YAML 드래프트 편집기</span>
        {isDraftDirty ? (
          <StatusPill intent="warning">저장 안 됨</StatusPill>
        ) : null}
        {isValidatedCurrent ? (
          validationView.isBlocked ? (
            <StatusPill intent="danger">검증 차단</StatusPill>
          ) : (
            <StatusPill intent="success">검증 통과</StatusPill>
          )
        ) : (
          <StatusPill intent="neutral">검증 필요</StatusPill>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground">
          {branch ? `편집 대상: ${branch.name}` : "편집 대상: 메인(읽기 전용)"}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <Separator />
        <div className="grid gap-3 p-3 lg:grid-cols-2">
          <div className="space-y-2">
            <Textarea
              value={yamlText}
              onChange={(event) => onYamlChange(event.target.value)}
              spellCheck={false}
              className="min-h-64 font-mono text-[11px]"
              aria-label="온톨로지 YAML 드래프트"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                onClick={handleValidate}
                disabled={validateMutation.isRunning}
              >
                <ShieldCheck />
                {validateMutation.isRunning ? "검증 중…" : "검증"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={!canSaveToBranch || updateMutation.isRunning}
                onClick={() => onSaveToBranch(yamlText)}
              >
                <Save />
                {updateMutation.isRunning ? "저장 중…" : "브랜치에 저장"}
              </Button>
              {branch ? (
                <span className="font-mono text-[10px] text-muted-foreground">
                  expected_fingerprint=
                  {truncateMiddle(branch.contentFingerprint, 16)}
                </span>
              ) : null}
            </div>
            {updateMutation.error ? (
              <ErrorState error={updateMutation.error} />
            ) : null}
            {updateMutation.result ? (
              <p className="font-mono text-[10px] text-success">
                브랜치 저장됨 · fingerprint=
                {truncateMiddle(updateMutation.result.contentFingerprint, 16)}
              </p>
            ) : null}
          </div>

          <div className="space-y-3">
            <ValidationResults
              view={validationView}
              result={validateMutation.result}
              error={validateMutation.error}
              isValidating={validateMutation.isRunning}
              requestId={validateMutation.requestId}
            />
            <Separator />
            <div className="space-y-2">
              <div className="section-label">적용 · 롤백</div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  disabled={!canApply}
                  onClick={() => void applyMutation.execute({ yamlText })}
                >
                  {applyMutation.isRunning ? "적용 중…" : "온톨로지에 적용"}
                </Button>
                {!isValidatedCurrent ? (
                  <span className="text-[11px] text-muted-foreground">
                    현재 텍스트를 먼저 검증해야 적용할 수 있습니다.
                  </span>
                ) : validationView.isBlocked ? (
                  <span className="text-[11px] text-destructive">
                    차단된 변경이 있어 적용할 수 없습니다.
                  </span>
                ) : null}
              </div>
              {applyMutation.error ? (
                <ErrorState error={applyMutation.error} />
              ) : null}
              {applyMutation.result ? (
                <div className="rounded border border-success/40 bg-success/10 p-2 text-[11px]">
                  <span className="font-medium text-success">
                    적용 완료 — v{applyMutation.result.versionNumber} 활성화
                  </span>
                  <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                    version_id=
                    {truncateMiddle(applyMutation.result.ontologyVersionId, 28)}
                  </div>
                </div>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  value={rollbackTarget}
                  onChange={(event) => setRollbackTarget(event.target.value)}
                  placeholder={
                    activeVersionNumber !== null && activeVersionNumber > 1
                      ? `롤백 버전 (예: ${activeVersionNumber - 1})`
                      : "롤백 버전 번호"
                  }
                  className="h-7 w-40 font-mono text-[11px]"
                  inputMode="numeric"
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    rollbackMutation.isRunning ||
                    Number.isNaN(Number.parseInt(rollbackTarget, 10))
                  }
                  onClick={handleRollback}
                >
                  <Undo2 />
                  {rollbackMutation.isRunning ? "롤백 중…" : "롤백"}
                </Button>
              </div>
              {rollbackMutation.error ? (
                <ErrorState error={rollbackMutation.error} />
              ) : null}
              {rollbackMutation.result ? (
                <div className="rounded border border-success/40 bg-success/10 p-2 text-[11px]">
                  <span className="font-medium text-success">
                    롤백 완료 — v
                    {rollbackMutation.result.rolledBackFromVersionNumber} → v
                    {rollbackMutation.result.rolledBackToVersionNumber} (새 버전
                    v{rollbackMutation.result.versionNumber})
                  </span>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
