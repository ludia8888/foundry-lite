import type { ProjectFolder, ResourceProject } from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteSession,
} from "@foundry-lite/sdk/react";
import { useEffect, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export type CatalogCreateMode = "project" | "folder";

const ROOT_FOLDER_VALUE = "__root__";

interface CreatedEvidence {
  mode: CatalogCreateMode;
  id: string;
  rid: string;
  name: string;
  idempotencyKeyUsed: string;
}

interface CatalogCreateDialogProps {
  mode: CatalogCreateMode | null;
  activeProject: ResourceProject | null;
  folders: ProjectFolder[];
  onClose: () => void;
  onCreated: (created: { mode: CatalogCreateMode; id: string }) => void;
}

function EvidenceBox({ evidence }: { evidence: CreatedEvidence }) {
  const session = useFoundryLiteSession();
  return (
    <div className="space-y-1 rounded border border-success/30 bg-success/5 p-2.5">
      <div className="flex items-center gap-2 text-[12px] font-medium text-success">
        생성 완료 <StatusPill intent="success">success</StatusPill>
      </div>
      <div className="space-y-0.5 font-mono text-[11px] text-muted-foreground">
        <div>name={evidence.name}</div>
        <div>id={evidence.id}</div>
        <div className="break-all">rid={evidence.rid}</div>
        <div className="break-all">
          idempotency_key={evidence.idempotencyKeyUsed}
        </div>
        {session.lastRequestId ? (
          <div>request_id={session.lastRequestId}</div>
        ) : null}
      </div>
    </div>
  );
}

/** 새 프로젝트/폴더 생성 다이얼로그: resources.projects.create / folders.create. */
export function CatalogCreateDialog({
  mode,
  activeProject,
  folders,
  onClose,
  onCreated,
}: CatalogCreateDialogProps) {
  const client = useFoundryLiteClient();
  const [name, setName] = useState("");
  const [parentFolderId, setParentFolderId] = useState(ROOT_FOLDER_VALUE);
  const [evidence, setEvidence] = useState<CreatedEvidence | null>(null);

  useEffect(() => {
    if (mode !== null) {
      setName("");
      setParentFolderId(ROOT_FOLDER_VALUE);
      setEvidence(null);
    }
  }, [mode]);

  const mutation = useFoundryLiteMutation(
    async (displayName: string) => {
      // HTTP 헤더는 ISO-8859-1만 허용하므로 한글 이름은 인코딩해 키에 넣는다.
      const keySeed = encodeURIComponent(displayName);
      if (mode === "project") {
        const key = idempotencyKey("resources.projects.create", keySeed);
        const project = await client.resources.projects.create(
          { displayName },
          { idempotencyKey: key },
        );
        return {
          mode: "project" as const,
          id: project.id,
          rid: project.rid,
          name: project.displayName,
          idempotencyKeyUsed: key,
        };
      }
      if (!activeProject) return null;
      const key = idempotencyKey("resources.folders.create", keySeed);
      const folder = await client.resources.folders.create(
        activeProject.id,
        {
          displayName,
          parentFolderId:
            parentFolderId === ROOT_FOLDER_VALUE ? null : parentFolderId,
        },
        { idempotencyKey: key },
      );
      return {
        mode: "folder" as const,
        id: folder.id,
        rid: folder.rid,
        name: folder.displayName,
        idempotencyKeyUsed: key,
      };
    },
    {
      lockKey: (displayName) => `catalog-create-${mode}-${displayName}`,
      onSuccess: (created) => {
        if (!created) return;
        setEvidence(created);
        onCreated({ mode: created.mode, id: created.id });
      },
    },
  );

  const activeFolders = folders.filter((folder) => folder.status === "active");
  const canSubmit =
    name.trim() !== "" &&
    (mode === "project" || activeProject !== null) &&
    !mutation.isRunning;

  return (
    <Dialog open={mode !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-sm">
        {mode !== null ? (
          <>
            <DialogHeader>
              <DialogTitle className="text-[14px]">
                {mode === "project" ? "새 프로젝트" : "새 폴더"}
              </DialogTitle>
              <DialogDescription className="text-[12px]">
                {mode === "project"
                  ? "resources.projects.create — Idempotency-Key와 함께 생성합니다."
                  : `resources.folders.create — ${activeProject?.displayName ?? "프로젝트"} 아래에 생성합니다.`}
              </DialogDescription>
            </DialogHeader>
            {evidence ? (
              <EvidenceBox evidence={evidence} />
            ) : (
              <div className="space-y-3">
                <div className="space-y-1">
                  <Label
                    htmlFor="catalog-create-name"
                    className="text-[12px]"
                  >
                    이름
                  </Label>
                  <Input
                    id="catalog-create-name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder={
                      mode === "project" ? "프로젝트 이름" : "폴더 이름"
                    }
                    className="h-7 text-[12px]"
                  />
                </div>
                {mode === "folder" ? (
                  <div className="space-y-1">
                    <Label
                      htmlFor="catalog-create-parent-folder"
                      className="text-[12px]"
                    >
                      상위 폴더
                    </Label>
                    <Select
                      value={parentFolderId}
                      onValueChange={setParentFolderId}
                    >
                      <SelectTrigger
                        id="catalog-create-parent-folder"
                        size="sm"
                        className="h-7 w-full text-[12px]"
                      >
                        <SelectValue placeholder="상위 폴더 선택" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={ROOT_FOLDER_VALUE}>
                          최상위
                        </SelectItem>
                        {activeFolders.map((folder) => (
                          <SelectItem key={folder.id} value={folder.id}>
                            {folder.displayName}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                ) : null}
              </div>
            )}
            {mutation.error ? <ErrorState error={mutation.error} /> : null}
            <DialogFooter>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-[12px]"
                onClick={onClose}
              >
                {evidence ? "닫기" : "취소"}
              </Button>
              {!evidence ? (
                <Button
                  size="sm"
                  className="h-7 text-[12px]"
                  disabled={!canSubmit}
                  onClick={() => void mutation.execute(name.trim())}
                >
                  {mutation.isRunning ? "생성 중…" : "생성"}
                </Button>
              ) : null}
            </DialogFooter>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
