import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import type { PipelineActions } from "../use-pipeline-actions";

interface BranchCreateDialogProps {
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
  actions: PipelineActions;
  defaultPipelineId?: string;
}

/** 새 브랜치 생성 다이얼로그. idempotency key evidence를 함께 노출한다. */
export function BranchCreateDialog({
  isOpen,
  onOpenChange,
  actions,
  defaultPipelineId,
}: BranchCreateDialogProps) {
  const [pipelineId, setPipelineId] = useState(
    defaultPipelineId ?? "pipeline-demo",
  );
  const [name, setName] = useState("");
  const mutation = actions.createBranch;
  const canSubmit = pipelineId.trim().length > 0 && name.trim().length > 0;

  const handleSubmit = async () => {
    const created = await mutation.execute({
      pipelineId: pipelineId.trim(),
      name: name.trim(),
    });
    if (created) {
      setName("");
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-[15px]">새 브랜치 만들기</DialogTitle>
          <DialogDescription className="text-xs">
            브랜치에서 그래프를 편집한 뒤 변경 제안으로 리뷰를 요청합니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-[11px] text-muted-foreground">
              파이프라인 ID
            </Label>
            <Input
              className="h-8 font-mono text-[12px]"
              value={pipelineId}
              onChange={(event) => setPipelineId(event.target.value)}
              placeholder="pipeline-demo"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-[11px] text-muted-foreground">
              브랜치 이름
            </Label>
            <Input
              className="h-8 text-[12px]"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="feature/주문-정제"
            />
          </div>
          {mutation.error ? (
            <p className="text-[11px] text-destructive">
              생성 실패 · {mutation.error.code}
              {mutation.error.requestId
                ? ` · req=${mutation.error.requestId}`
                : ""}
              {mutation.retryable ? " · 재시도 가능" : ""}
            </p>
          ) : null}
          {actions.lastIdempotencyKey ? (
            <p className="truncate font-mono text-[10px] text-muted-foreground">
              idempotency={actions.lastIdempotencyKey}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button
            size="sm"
            variant="outline"
            className="text-[12px]"
            onClick={() => onOpenChange(false)}
          >
            취소
          </Button>
          <Button
            size="sm"
            className="text-[12px]"
            disabled={!canSubmit || mutation.isRunning}
            onClick={() => void handleSubmit()}
          >
            {mutation.isRunning ? "생성 중..." : "브랜치 생성"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
