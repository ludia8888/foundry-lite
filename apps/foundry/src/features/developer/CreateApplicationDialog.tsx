import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
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

import { toApplicationRow, type OsdkApplicationRow } from "./developer-model";

interface CreateApplicationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (app: OsdkApplicationRow) => void;
}

const API_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9_]*$/;

/**
 * 새 OSDK 애플리케이션 생성 다이얼로그.
 * developerConsole.osdkApplications.create (idempotency key + request id 증거).
 */
export function CreateApplicationDialog({
  open,
  onOpenChange,
  onCreated,
}: CreateApplicationDialogProps) {
  const client = useFoundryLiteClient();
  const [appApiName, setAppApiName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [lastKey, setLastKey] = useState<string | null>(null);

  const mutation = useFoundryLiteMutation(
    ({ apiName, name, key }: { apiName: string; name: string; key: string }) =>
      client.developerConsole.osdkApplications.create(
        { appApiName: apiName, displayName: name },
        { idempotencyKey: key },
      ),
    { lockKey: ({ apiName }) => `developer:app:create:${apiName}` },
  );

  const trimmedApiName = appApiName.trim();
  const isApiNameValid =
    trimmedApiName.length === 0 || API_NAME_PATTERN.test(trimmedApiName);
  const canSubmit =
    trimmedApiName.length > 0 &&
    isApiNameValid &&
    displayName.trim().length > 0 &&
    !mutation.isRunning;

  const reset = () => {
    setAppApiName("");
    setDisplayName("");
    setLastKey(null);
  };

  const handleSubmit = async () => {
    const key = idempotencyKey("osdk_application_create", trimmedApiName);
    setLastKey(key);
    const created = await mutation.execute({
      apiName: trimmedApiName,
      name: displayName.trim(),
      key,
    });
    if (created) {
      onCreated(toApplicationRow(created.application));
      reset();
      onOpenChange(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>새 OSDK 애플리케이션</DialogTitle>
          <DialogDescription>
            온톨로지 위에 SDK를 발급할 애플리케이션을 등록합니다. API name은
            생성 후 변경할 수 없습니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="app-api-name" className="section-label">
              애플리케이션 API name
            </Label>
            <Input
              id="app-api-name"
              value={appApiName}
              onChange={(event) => setAppApiName(event.target.value)}
              placeholder="orderPortal"
              className="font-mono"
              aria-invalid={!isApiNameValid}
            />
            {!isApiNameValid ? (
              <p className="text-[11px] text-destructive">
                영문자로 시작하고 영문/숫자/밑줄만 사용할 수 있습니다.
              </p>
            ) : (
              <p className="text-[11px] text-muted-foreground">
                패턴: 영문자 시작, 이후 [A-Za-z0-9_].
              </p>
            )}
          </div>
          <div className="space-y-1">
            <Label htmlFor="app-display-name" className="section-label">
              표시 이름
            </Label>
            <Input
              id="app-display-name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="Order Portal"
            />
          </div>
          {mutation.error ? <ErrorState error={mutation.error} /> : null}
          {lastKey ? (
            <div className="rounded border bg-muted/40 px-2 py-1 font-mono text-[11px] text-muted-foreground">
              idempotency_key={lastKey}
            </div>
          ) : null}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isRunning}
          >
            취소
          </Button>
          <Button
            size="sm"
            onClick={() => void handleSubmit()}
            disabled={!canSubmit}
          >
            애플리케이션 생성
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
