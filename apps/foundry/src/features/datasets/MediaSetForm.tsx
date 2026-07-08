import type { MediaSet, MediaSetCreateRequest } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { FileStack, FolderSearch, Loader2, Plus } from "lucide-react";
import { useCallback, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import {
  MEDIA_DEFAULT_ALLOWED_FORMATS,
  MEDIA_DEFAULT_CLASSIFICATION,
  MEDIA_DEFAULT_FORMAT,
  MEDIA_DEFAULT_SCHEMA_TYPE,
} from "./media-constants";
import { useScreenQuery } from "./use-screen-query";

interface MediaSetFormProps {
  onRegister: (mediaSet: MediaSet) => void;
}

/** 미디어 세트 생성 + ID로 불러오기. 백엔드에 목록 API가 없어 세션 레지스트리에 등록해 사용한다. */
export function MediaSetForm({ onRegister }: MediaSetFormProps) {
  return (
    <div className="max-w-2xl space-y-4 p-4">
      <div>
        <h2 className="text-[13px] font-semibold">미디어 세트 작업 공간</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          서버에 미디어 세트 목록 API가 없어(향후 범위) 새로 생성하거나 ID로
          불러온 세트만 카탈로그에 등록됩니다.
        </p>
      </div>
      <CreateMediaSetCard onRegister={onRegister} />
      <LoadMediaSetCard onRegister={onRegister} />
    </div>
  );
}

function CreateMediaSetCard({ onRegister }: MediaSetFormProps) {
  const client = useFoundryLiteClient();
  const [namespace, setNamespace] = useState("docs");
  const [name, setName] = useState("");
  const createMutation = useFoundryLiteMutation(
    (payload: MediaSetCreateRequest) => client.media.sets.create(payload),
    { onSuccess: onRegister },
  );

  const canSubmit =
    namespace.trim().length > 0 &&
    name.trim().length > 0 &&
    !createMutation.isRunning;

  const handleCreate = () => {
    if (!canSubmit) return;
    void createMutation.execute({
      namespace: namespace.trim(),
      name: name.trim(),
      schemaType: MEDIA_DEFAULT_SCHEMA_TYPE,
      primaryFormat: MEDIA_DEFAULT_FORMAT,
      allowedInputFormats: MEDIA_DEFAULT_ALLOWED_FORMATS,
      classification: MEDIA_DEFAULT_CLASSIFICATION,
    });
  };

  return (
    <section className="space-y-3 rounded border bg-card p-3">
      <div className="flex items-center gap-2">
        <Plus className="size-4 text-primary" />
        <span className="section-label">새 미디어 세트 생성</span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label htmlFor="media-set-namespace" className="text-xs">
            네임스페이스
          </Label>
          <Input
            id="media-set-namespace"
            value={namespace}
            onChange={(event) => setNamespace(event.target.value)}
            className="h-7 text-xs"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="media-set-name" className="text-xs">
            이름
          </Label>
          <Input
            id="media-set-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="예: contracts"
            className="h-7 text-xs"
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <StatusPill intent="neutral">
          schema={MEDIA_DEFAULT_SCHEMA_TYPE}
        </StatusPill>
        <StatusPill intent="neutral">format={MEDIA_DEFAULT_FORMAT}</StatusPill>
        <StatusPill intent="neutral">
          classification={MEDIA_DEFAULT_CLASSIFICATION}
        </StatusPill>
      </div>
      {createMutation.error ? (
        <ErrorState error={createMutation.error} onRetry={handleCreate} />
      ) : null}
      <Button size="sm" onClick={handleCreate} disabled={!canSubmit}>
        {createMutation.isRunning ? (
          <Loader2 className="animate-spin" />
        ) : (
          <FileStack />
        )}
        미디어 세트 생성
      </Button>
    </section>
  );
}

function LoadMediaSetCard({ onRegister }: MediaSetFormProps) {
  const client = useFoundryLiteClient();
  const [mediaSetIdInput, setMediaSetIdInput] = useState("");
  const [submittedId, setSubmittedId] = useState<string | null>(null);

  const loadMediaSet = useCallback(
    () => client.media.sets.get(submittedId ?? ""),
    [client, submittedId],
  );
  const query = useScreenQuery(["media", "sets", submittedId], loadMediaSet, {
    enabled: submittedId !== null,
    onSuccess: onRegister,
  });

  const handleLoad = () => {
    const trimmed = mediaSetIdInput.trim();
    if (trimmed.length === 0) return;
    setSubmittedId(trimmed);
  };

  return (
    <section className="space-y-3 rounded border bg-card p-3">
      <div className="flex items-center gap-2">
        <FolderSearch className="size-4 text-primary" />
        <span className="section-label">ID로 불러오기</span>
      </div>
      <div className="flex items-center gap-2">
        <Input
          value={mediaSetIdInput}
          onChange={(event) => setMediaSetIdInput(event.target.value)}
          placeholder="mset_..."
          className="h-7 font-mono text-[11px]"
        />
        <Button
          size="sm"
          variant="outline"
          onClick={handleLoad}
          disabled={mediaSetIdInput.trim().length === 0 || query.isLoading}
        >
          {query.isLoading && submittedId !== null ? (
            <Loader2 className="animate-spin" />
          ) : null}
          불러오기
        </Button>
      </div>
      {submittedId !== null && query.error ? (
        <ErrorState error={query.error} onRetry={query.reload} />
      ) : null}
    </section>
  );
}
