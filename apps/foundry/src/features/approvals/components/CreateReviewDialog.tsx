import type { InsightReviewPayload } from "@foundry-lite/sdk";
import { createRequestId } from "@foundry-lite/sdk";
import type { FoundryLiteApiError } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteInsightReviewCreate,
} from "@foundry-lite/sdk/react";
import { Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface CreateReviewDialogProps {
  onCreated: (reviewId: string) => void;
}

/** 인사이트 리뷰 생성 다이얼로그 (user flow 시작점). */
export function CreateReviewDialog({ onCreated }: CreateReviewDialogProps) {
  const [open, setOpen] = useState(false);
  const [claimText, setClaimText] = useState("");
  const [evidenceObjectId, setEvidenceObjectId] = useState("O-1001");

  const client = useFoundryLiteClient();
  const create = useFoundryLiteInsightReviewCreate(client, {
    onSuccess: (review: InsightReviewPayload) => {
      toast.success("인사이트 리뷰를 생성했습니다");
      onCreated(review.id);
      setOpen(false);
      setClaimText("");
    },
    onError: (error: FoundryLiteApiError) =>
      toast.error(`생성 실패: ${error.code}`),
  });

  const handleSubmit = () => {
    const text = claimText.trim();
    if (!text) {
      toast.error("클레임 텍스트를 입력하세요");
      return;
    }
    const objectId = evidenceObjectId.trim();
    void create.execute({
      claimId: `claim-${Date.now()}`,
      claimText: text,
      evidenceObjectIds: objectId ? [objectId] : [],
      evidenceRefs: objectId
        ? [{ kind: "object", objectType: "Order", objectId }]
        : [],
      priority: "high",
      idempotencyKey: createRequestId("review-create"),
    });
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus />
          인사이트 리뷰 생성
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>인사이트 리뷰 생성</DialogTitle>
          <DialogDescription>
            AI 인사이트 클레임을 리뷰 큐에 등록합니다. 생성 후 배정 ·
            승인/거절할 수 있습니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="claim-text" className="text-[12px]">
              클레임 텍스트
            </Label>
            <Textarea
              id="claim-text"
              value={claimText}
              onChange={(event) => setClaimText(event.target.value)}
              placeholder="예: O-1001 주문은 마진 임계값을 충족하여 승인 대상입니다"
              className="min-h-[72px] text-[12px]"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="evidence-object" className="text-[12px]">
              증거 객체 ID (선택)
            </Label>
            <Input
              id="evidence-object"
              value={evidenceObjectId}
              onChange={(event) => setEvidenceObjectId(event.target.value)}
              placeholder="O-1001"
              className="h-8 text-[12px]"
            />
          </div>
          {create.error ? <ErrorState error={create.error} /> : null}
        </div>
        <DialogFooter>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={create.isRunning}
          >
            취소
          </Button>
          <Button size="sm" onClick={handleSubmit} disabled={create.isRunning}>
            생성
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
