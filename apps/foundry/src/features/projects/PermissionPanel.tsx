import type {
  ProjectGrant,
  ProjectRole,
  ResourceProject,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { ShieldCheck, UserPlus, Users } from "lucide-react";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DEMO_CONTEXT } from "@/lib/api";

import { useProjectsQuery } from "./use-projects-query";

const ROLE_OPTIONS: readonly { value: ProjectRole; label: string }[] = [
  { value: "owner", label: "Owner" },
  { value: "editor", label: "Editor" },
  { value: "viewer", label: "Viewer" },
  { value: "discoverer", label: "Discoverer" },
];

const ROLE_LABEL: Record<ProjectRole, string> = {
  owner: "Owner",
  editor: "Editor",
  viewer: "Viewer",
  discoverer: "Discoverer",
};

interface PermissionPanelProps {
  project: ResourceProject | null;
}

/**
 * 역할(Roles) 패널: 기본 역할 안내 + projects.listGrants 실데이터 + upsertGrant로
 * principal별 역할을 직접 변경한다. 리소스 단위 직접 grant override는 future.
 */
export function PermissionPanel({ project }: PermissionPanelProps) {
  const client = useFoundryLiteClient();
  const projectId = project?.id ?? null;

  const loadGrants = useCallback(
    () =>
      projectId === null
        ? Promise.resolve([] as ProjectGrant[])
        : client.resources.projects.listGrants(projectId),
    [client, projectId],
  );
  const grantsQuery = useProjectsQuery(
    ["projects", "grants", projectId],
    loadGrants,
  );
  const grants = grantsQuery.data ?? [];

  const [pendingPrincipal, setPendingPrincipal] = useState<string | null>(null);
  const [newPrincipalId, setNewPrincipalId] = useState("");
  const [newRole, setNewRole] = useState<ProjectRole>("viewer");

  const upsertGrant = useCallback(
    async (principalId: string, role: ProjectRole) => {
      if (projectId === null) return;
      setPendingPrincipal(principalId);
      try {
        await client.resources.projects.upsertGrant(
          projectId,
          "user",
          principalId,
          { role },
          {
            idempotencyKey: idempotencyKey(
              "project-grant",
              `${projectId}:${principalId}:${role}:${crypto.randomUUID()}`,
            ),
          },
        );
        toast.success(
          `${principalId} → ${ROLE_LABEL[role]} 역할로 저장했습니다.`,
        );
        await grantsQuery.reload();
      } catch (caught) {
        toast.error("역할 저장에 실패했습니다.");
        throw caught;
      } finally {
        setPendingPrincipal(null);
      }
    },
    [client, projectId, grantsQuery],
  );

  const handleAddGrant = () => {
    const principalId = newPrincipalId.trim();
    if (principalId.length === 0) return;
    void upsertGrant(principalId, newRole).then(() => setNewPrincipalId(""));
  };

  if (project === null) {
    return (
      <div className="rounded border border-dashed p-2.5 text-[11px] text-muted-foreground">
        프로젝트에 등록되지 않은 리소스입니다. 카탈로그 동기화 후 프로젝트
        역할이 적용됩니다.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section className="space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-[13px] font-semibold">기본 역할</span>
          <StatusPill intent="neutral">Discoverer</StatusPill>
        </div>
        <p className="text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">
            {project.displayName}
          </span>{" "}
          멤버는 이 프로젝트의 존재를 볼 수 있으며 Discoverer 역할이 부여됩니다.
          리소스 권한은 이 프로젝트 역할에서 상속됩니다.
        </p>
      </section>

      <section className="space-y-2">
        <span className="text-[13px] font-semibold">역할</span>
        <div className="flex items-center gap-1.5">
          <div className="relative min-w-0 flex-1">
            <UserPlus className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={newPrincipalId}
              onChange={(event) => setNewPrincipalId(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") handleAddGrant();
              }}
              placeholder="사용자 또는 그룹 추가..."
              className="h-7 pl-7 text-[12px]"
            />
          </div>
          <Select
            value={newRole}
            onValueChange={(value) => setNewRole(value as ProjectRole)}
          >
            <SelectTrigger size="sm" className="h-7 w-24 text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ROLE_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            className="h-7 px-2.5 text-[12px]"
            disabled={
              newPrincipalId.trim().length === 0 || pendingPrincipal !== null
            }
            onClick={handleAddGrant}
          >
            추가
          </Button>
        </div>

        {grantsQuery.isLoading ? (
          <LoadingState rowCount={2} />
        ) : grantsQuery.error ? (
          <ErrorState
            error={grantsQuery.error}
            onRetry={() => void grantsQuery.reload()}
          />
        ) : grants.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            아직 부여된 역할이 없습니다. 위에서 사용자나 그룹을 추가하세요.
          </p>
        ) : (
          <div className="space-y-1">
            {grants.map((grant) => (
              <GrantRow
                key={grant.id}
                grant={grant}
                isPending={pendingPrincipal === grant.principalId}
                isSelf={grant.principalId === DEMO_CONTEXT.userId}
                onChangeRole={(role) =>
                  void upsertGrant(grant.principalId, role)
                }
              />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="section-label">직접 GRANT (리소스 단위)</span>
          <StatusPill intent="neutral">future</StatusPill>
        </div>
        <div className="flex items-start gap-2 rounded border border-dashed p-2.5">
          <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
          <p className="text-[11px] text-muted-foreground">
            폴더/리소스 단위 직접 grant override와 접근 요청 워크플로는 아직
            지원되지 않습니다. 현재는 프로젝트 역할 중심 권한만 가능합니다.
          </p>
        </div>
      </section>
    </div>
  );
}

function GrantRow({
  grant,
  isPending,
  isSelf,
  onChangeRole,
}: {
  grant: ProjectGrant;
  isPending: boolean;
  isSelf: boolean;
  onChangeRole: (role: ProjectRole) => void;
}) {
  return (
    <div className="flex items-center gap-2 rounded border px-2 py-1.5">
      <Users className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
        {grant.principalType}:{grant.principalId}
      </span>
      {isSelf ? <StatusPill intent="info">나</StatusPill> : null}
      <Select
        value={grant.role}
        onValueChange={(value) => onChangeRole(value as ProjectRole)}
        disabled={isPending}
      >
        <SelectTrigger size="sm" className="h-6 w-24 text-[11px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {ROLE_OPTIONS.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
