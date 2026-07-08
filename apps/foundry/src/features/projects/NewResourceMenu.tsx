import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteSession,
} from "@foundry-lite/sdk/react";
import {
  AppWindow,
  Briefcase,
  ChevronDown,
  Database,
  FolderPlus,
  GitBranch,
  Layers,
  Plus,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";

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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type CreatableKind = "pipeline_branch" | "object_set" | "osdk_app";

const CREATABLE_META: Record<
  CreatableKind,
  { title: string; description: string }
> = {
  pipeline_branch: {
    title: "새 파이프라인 브랜치",
    description:
      "pipelines.branches.create — idempotency key와 함께 생성합니다.",
  },
  object_set: {
    title: "새 객체 세트",
    description: "objectSets.create — static 세트를 생성합니다.",
  },
  osdk_app: {
    title: "새 OSDK 애플리케이션",
    description:
      "developerConsole.osdkApplications.create — idempotency key와 함께 생성합니다.",
  },
};

interface CreateSuccessEvidence {
  createdName: string;
  createdId: string;
  idempotencyKeyUsed: string | null;
}

interface NewResourceMenuProps {
  objectTypeApiNames: string[];
  onCreated: () => void;
  onCreateProject: () => void;
  onCreateFolder: () => void;
  canCreateFolder: boolean;
}

interface CreateFormFields {
  name: string;
  pipelineId: string;
  objectType: string;
  appApiName: string;
}

const INITIAL_FIELDS: CreateFormFields = {
  name: "",
  pipelineId: "pipeline-demo",
  objectType: "",
  appApiName: "",
};

function useCreateResource(
  kind: CreatableKind | null,
  onEvidence: (evidence: CreateSuccessEvidence) => void,
) {
  const client = useFoundryLiteClient();

  return useFoundryLiteMutation(
    async (fields: CreateFormFields) => {
      if (kind === "pipeline_branch") {
        // HTTP 헤더는 ISO-8859-1만 허용하므로 한글 이름은 인코딩해 키에 넣는다.
        const key = idempotencyKey(
          "projects.create_pipeline_branch",
          encodeURIComponent(fields.name),
        );
        const branch = await client.pipelines.branches.create(
          { pipelineId: fields.pipelineId, name: fields.name },
          { idempotencyKey: key },
        );
        return { createdName: fields.name, createdId: branch.id, key };
      }
      if (kind === "object_set") {
        const objectSet = await client.objectSets.create({
          name: fields.name,
          objectType: fields.objectType,
          setType: "static",
          ids: [],
        });
        return {
          createdName: objectSet.name,
          createdId: objectSet.id,
          key: null,
        };
      }
      const key = idempotencyKey("projects.create_osdk_app", fields.appApiName);
      const app = await client.developerConsole.osdkApplications.create(
        { appApiName: fields.appApiName, displayName: fields.name },
        { idempotencyKey: key },
      );
      const appRecord = app.application;
      const createdId =
        typeof appRecord["id"] === "string"
          ? (appRecord["id"] as string)
          : fields.appApiName;
      return { createdName: fields.name, createdId, key };
    },
    {
      lockKey: (fields) => `projects-create-${kind}-${fields.name}`,
      onSuccess: (created) => {
        if (!created) return;
        onEvidence({
          createdName: created.createdName,
          createdId: created.createdId,
          idempotencyKeyUsed: created.key,
        });
      },
    },
  );
}

function CreateFormBody({
  kind,
  fields,
  onFieldsChange,
  objectTypeApiNames,
}: {
  kind: CreatableKind;
  fields: CreateFormFields;
  onFieldsChange: (fields: CreateFormFields) => void;
  objectTypeApiNames: string[];
}) {
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="text-[12px]">이름</Label>
        <Input
          value={fields.name}
          onChange={(event) =>
            onFieldsChange({ ...fields, name: event.target.value })
          }
          placeholder={kind === "osdk_app" ? "표시 이름" : "리소스 이름"}
          className="h-7 text-[12px]"
        />
      </div>
      {kind === "pipeline_branch" ? (
        <div className="space-y-1">
          <Label className="text-[12px]">파이프라인 ID</Label>
          <Input
            value={fields.pipelineId}
            onChange={(event) =>
              onFieldsChange({ ...fields, pipelineId: event.target.value })
            }
            className="h-7 font-mono text-[11px]"
          />
        </div>
      ) : null}
      {kind === "object_set" ? (
        <div className="space-y-1">
          <Label className="text-[12px]">객체 타입</Label>
          <Select
            value={fields.objectType}
            onValueChange={(value) =>
              onFieldsChange({ ...fields, objectType: value })
            }
          >
            <SelectTrigger size="sm" className="h-7 w-full text-[12px]">
              <SelectValue placeholder="객체 타입 선택" />
            </SelectTrigger>
            <SelectContent>
              {objectTypeApiNames.map((apiName) => (
                <SelectItem key={apiName} value={apiName}>
                  {apiName}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}
      {kind === "osdk_app" ? (
        <div className="space-y-1">
          <Label className="text-[12px]">앱 API 이름</Label>
          <Input
            value={fields.appApiName}
            onChange={(event) =>
              onFieldsChange({ ...fields, appApiName: event.target.value })
            }
            placeholder="예: order-ops-app"
            className="h-7 font-mono text-[11px]"
          />
        </div>
      ) : null}
    </div>
  );
}

function SuccessEvidenceBox({ evidence }: { evidence: CreateSuccessEvidence }) {
  const session = useFoundryLiteSession();
  return (
    <div className="space-y-1 rounded border border-success/30 bg-success/5 p-2.5">
      <div className="flex items-center gap-2 text-[12px] font-medium text-success">
        생성 완료 <StatusPill intent="success">success</StatusPill>
      </div>
      <div className="space-y-0.5 font-mono text-[11px] text-muted-foreground">
        <div>name={evidence.createdName}</div>
        <div>id={evidence.createdId}</div>
        {evidence.idempotencyKeyUsed ? (
          <div className="break-all">
            idempotency_key={evidence.idempotencyKeyUsed}
          </div>
        ) : (
          <div>이 endpoint는 idempotency key를 받지 않습니다.</div>
        )}
        {session.lastRequestId ? (
          <div>request_id={session.lastRequestId}</div>
        ) : null}
      </div>
    </div>
  );
}

/** 상단 "새로 만들기" 메뉴: 실존 mutation 3종 + 프로젝트/폴더 생성. */
export function NewResourceMenu({
  objectTypeApiNames,
  onCreated,
  onCreateProject,
  onCreateFolder,
  canCreateFolder,
}: NewResourceMenuProps) {
  const navigate = useNavigate();
  const [dialogKind, setDialogKind] = useState<CreatableKind | null>(null);
  const [fields, setFields] = useState<CreateFormFields>(INITIAL_FIELDS);
  const [evidence, setEvidence] = useState<CreateSuccessEvidence | null>(null);
  const mutation = useCreateResource(dialogKind, setEvidence);

  const canSubmit = useMemo(() => {
    if (!dialogKind || fields.name.trim() === "") return false;
    if (dialogKind === "pipeline_branch")
      return fields.pipelineId.trim() !== "";
    if (dialogKind === "object_set") return fields.objectType !== "";
    return fields.appApiName.trim() !== "";
  }, [dialogKind, fields]);

  const handleOpenDialog = (kind: CreatableKind) => {
    setFields(INITIAL_FIELDS);
    setEvidence(null);
    setDialogKind(kind);
  };

  const handleClose = () => {
    if (evidence) onCreated();
    setDialogKind(null);
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="sm" className="h-7 text-[12px]">
            <Plus className="size-3.5" />
            새로 만들기
            <ChevronDown className="size-3" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64">
          <DropdownMenuLabel className="section-label">
            지금 생성 가능
          </DropdownMenuLabel>
          <DropdownMenuItem onClick={() => handleOpenDialog("pipeline_branch")}>
            <GitBranch className="size-3.5" />
            파이프라인 브랜치
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => handleOpenDialog("object_set")}>
            <Layers className="size-3.5" />
            객체 세트
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => handleOpenDialog("osdk_app")}>
            <AppWindow className="size-3.5" />
            OSDK 애플리케이션
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => navigate("/data/connections")}>
            <Database className="size-3.5" />
            데이터셋 (소스 온보딩으로 이동)
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuLabel className="section-label">
            카탈로그
          </DropdownMenuLabel>
          <DropdownMenuItem onClick={onCreateProject}>
            <Briefcase className="size-3.5" />
            프로젝트
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={!canCreateFolder}
            onClick={onCreateFolder}
          >
            <FolderPlus className="size-3.5" />
            폴더
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog
        open={dialogKind !== null}
        onOpenChange={(open) => !open && handleClose()}
      >
        <DialogContent className="max-w-sm">
          {dialogKind ? (
            <>
              <DialogHeader>
                <DialogTitle className="text-[14px]">
                  {CREATABLE_META[dialogKind].title}
                </DialogTitle>
                <DialogDescription className="text-[12px]">
                  {CREATABLE_META[dialogKind].description}
                </DialogDescription>
              </DialogHeader>
              {evidence ? (
                <SuccessEvidenceBox evidence={evidence} />
              ) : (
                <CreateFormBody
                  kind={dialogKind}
                  fields={fields}
                  onFieldsChange={setFields}
                  objectTypeApiNames={objectTypeApiNames}
                />
              )}
              {mutation.error ? <ErrorState error={mutation.error} /> : null}
              <DialogFooter>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-[12px]"
                  onClick={handleClose}
                >
                  {evidence ? "닫고 목록 새로고침" : "취소"}
                </Button>
                {!evidence ? (
                  <Button
                    size="sm"
                    className="h-7 text-[12px]"
                    disabled={!canSubmit || mutation.isRunning}
                    onClick={() => void mutation.execute(fields)}
                  >
                    {mutation.isRunning ? "생성 중…" : "생성"}
                  </Button>
                ) : null}
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
