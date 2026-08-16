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
import { Textarea } from "@/components/ui/textarea";

import type { PipelineActions } from "../use-pipeline-actions";

interface ProposeDialogProps {
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
  branchId: string | null;
  actions: PipelineActions;
}

/** 브랜치 변경 제안 다이얼로그. 제출 결과 request id / idempotency key evidence 표시. */
export function ProposeDialog({
  isOpen,
  onOpenChange,
  branchId,
  actions,
}: ProposeDialogProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const mutation = actions.propose;

  const handleSubmit = async () => {
    if (!branchId) return;
    const proposal = await mutation.execute({
      branchId,
      title: title.trim(),
      description: description.trim(),
    });
    if (proposal) {
      setTitle("");
      setDescription("");
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-[15px]">변경 제안 제출</DialogTitle>
          <DialogDescription className="text-xs">
            저장된 작업 테스트를 통과한 현재 작업을 리뷰어에게 보냅니다.
            승인 후 적용하면 새 버전이 생성됩니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-[11px] text-muted-foreground">제목</Label>
            <Input
              className="h-8 text-[12px]"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="예: 주문-고객 조인 파이프라인 추가"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-[11px] text-muted-foreground">
              설명 (선택)
            </Label>
            <Textarea
              className="min-h-20 text-[12px]"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="변경 내용과 검증 근거를 적어주세요."
            />
          </div>
          {mutation.error ? (
            <p className="text-[11px] text-destructive">
              제안 실패 · {mutation.error.code}
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
            disabled={
              !branchId || title.trim().length === 0 || mutation.isRunning
            }
            onClick={() => void handleSubmit()}
          >
            {mutation.isRunning ? "제출 중..." : "제안 제출"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
