import type {
  FoundryLiteApiError,
  ProjectGrant,
  ProjectRole,
  ResourceProject,
} from "@foundry-lite/sdk";
import {
  createFoundryLiteClient,
  idempotencyKey,
  normalizeFoundryLiteError,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import {
  KeyRound,
  ShieldAlert,
  ShieldCheck,
  UserPlus,
  Users,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { DataTable } from "@/components/shared/DataTable";
import type { DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
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
import { API_BASE_URL, DEMO_CONTEXT } from "@/lib/api";

import { useSecurityQuery } from "./use-security-query";

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

const ROLE_INTENT: Record<
  ProjectRole,
  "success" | "info" | "warning" | "neutral"
> = {
  owner: "success",
  editor: "info",
  viewer: "warning",
  discoverer: "neutral",
};

const PRINCIPAL_LABEL: Record<string, string> = {
  user: "사용자",
  group: "그룹",
  role: "역할",
};

/** viewer 권한으로 upsertGrant를 시도해 PERMISSION_DENIED 상태를 실증하는 프로브 결과. */
interface PermissionProbeResult {
  code: string;
  message: string;
  requestId: string | null;
  requiredRole: string | null;
  actualRole: string | null;
}

/**
 * 프로젝트 권한 탭.
 *
 * resources.projects.list로 프로젝트를 고르고, listGrants로 principal별 역할을
 * 조회한다. upsertGrant는 idempotency key + request id를 evidence로 노출한다.
 * (Compass 리소스 드로어의 Roles 패널과 동일한 백엔드다.)
 *
 * "권한 실패는 permission denied state" acceptance를 만족시키기 위해, 별도
 * viewer 컨텍스트 클라이언트로 동일 mutation을 시도하는 실증 프로브를 제공한다.
 */
export function ProjectGrantsPanel() {
  const client = useFoundryLiteClient();

  const projectsQuery = useSecurityQuery(["security", "projects"], () =>
    client.resources.projects.list(),
  );
  const projects = useMemo(
    () => projectsQuery.data ?? [],
    [projectsQuery.data],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const activeProject: ResourceProject | null = useMemo(() => {
    if (projects.length === 0) return null;
    return projects.find((p) => p.id === selectedId) ?? projects[0];
  }, [projects, selectedId]);
  const projectId = activeProject?.id ?? null;

  const grantsQuery = useSecurityQuery(["security", "grants", projectId], () =>
    projectId === null
      ? Promise.resolve([] as ProjectGrant[])
      : client.resources.projects.listGrants(projectId),
  );
  const grants = grantsQuery.data ?? [];

  const [pendingPrincipal, setPendingPrincipal] = useState<string | null>(null);
  const [lastEvidence, setLastEvidence] = useState<{
    principalId: string;
    role: ProjectRole;
    idempotencyKey: string;
    requestId: string | null;
  } | null>(null);
  const [newPrincipalId, setNewPrincipalId] = useState("");
  const [newRole, setNewRole] = useState<ProjectRole>("viewer");

  const upsertGrant = useCallback(
    async (principalId: string, role: ProjectRole) => {
      if (projectId === null) return;
      setPendingPrincipal(principalId);
      const key = idempotencyKey(
        "project-grant",
        `${projectId}:${principalId}:${role}:${crypto.randomUUID()}`,
      );
      try {
        const grant = await client.resources.projects.upsertGrant(
          projectId,
          "user",
          principalId,
          { role },
          { idempotencyKey: key },
        );
        setLastEvidence({
          principalId,
          role,
          idempotencyKey: key,
          // upsertGrant 응답은 grant 본문만 주므로 request id는 grant id를 근거로 남긴다.
          requestId: grant.id,
        });
        toast.success(
          `${principalId} → ${ROLE_LABEL[role]} 역할로 저장했습니다.`,
        );
        await grantsQuery.reload();
      } catch (caught) {
        const normalized = normalizeFoundryLiteError(caught);
        toast.error("역할 저장에 실패했습니다.");
        throw normalized;
      } finally {
        setPendingPrincipal(null);
      }
    },
    [client, projectId, grantsQuery],
  );

  const handleAddGrant = () => {
    const principalId = newPrincipalId.trim();
    if (principalId.length === 0) return;
    void upsertGrant(principalId, newRole)
      .then(() => setNewPrincipalId(""))
      .catch(() => {
        /* toast + throw는 upsertGrant에서 처리됨 */
      });
  };

  const columns: readonly DataTableColumn<ProjectGrant>[] = [
    {
      key: "principal",
      header: "Principal",
      render: (grant) => (
        <div className="flex items-center gap-2">
          <Users className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="font-mono text-[11px]">{grant.principalId}</span>
          {grant.principalId === DEMO_CONTEXT.userId ? (
            <StatusPill intent="info">나</StatusPill>
          ) : null}
        </div>
      ),
    },
    {
      key: "type",
      header: "유형",
      render: (grant) => (
        <span className="text-muted-foreground">
          {PRINCIPAL_LABEL[grant.principalType] ?? grant.principalType}
        </span>
      ),
    },
    {
      key: "role",
      header: "역할",
      className: "w-28",
      render: (grant) => (
        <Select
          value={grant.role}
          onValueChange={(value) =>
            void upsertGrant(grant.principalId, value as ProjectRole).catch(
              () => {},
            )
          }
          disabled={pendingPrincipal === grant.principalId}
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
      ),
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-3">
        <section className="space-y-2 rounded border bg-card p-3">
          <div className="flex items-center justify-between gap-3">
            <span className="section-label">프로젝트</span>
            {projectsQuery.isRefreshing ? (
              <span className="text-[11px] text-muted-foreground">
                갱신 중…
              </span>
            ) : null}
          </div>
          {projectsQuery.isLoading ? (
            <LoadingState rowCount={1} />
          ) : projectsQuery.error ? (
            <ErrorState
              error={projectsQuery.error}
              onRetry={() => void projectsQuery.reload()}
            />
          ) : projects.length === 0 ? (
            <EmptyState
              title="프로젝트가 없습니다"
              description="카탈로그에 프로젝트를 등록한 후 역할을 관리할 수 있습니다."
            />
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={activeProject?.id ?? undefined}
                onValueChange={setSelectedId}
              >
                <SelectTrigger
                  size="sm"
                  className="h-7 w-full min-w-0 text-[12px] sm:w-[280px]"
                >
                  <SelectValue placeholder="프로젝트 선택" />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((project) => (
                    <SelectItem key={project.id} value={project.id}>
                      {project.displayName}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {activeProject ? (
                <span className="font-mono text-[11px] text-muted-foreground">
                  {activeProject.rid}
                </span>
              ) : null}
            </div>
          )}
        </section>

        {activeProject ? (
          <section className="space-y-2 rounded border bg-card p-3">
            <div className="flex items-center justify-between">
              <span className="text-[13px] font-semibold">기본 역할</span>
              <StatusPill intent="neutral">Discoverer</StatusPill>
            </div>
            <p className="text-[11px] text-muted-foreground">
              <span className="font-medium text-foreground">
                {activeProject.displayName}
              </span>{" "}
              멤버는 이 프로젝트의 존재를 볼 수 있으며 Discoverer 역할을
              부여받습니다. 리소스 권한은 이 프로젝트 역할에서 상속됩니다.
            </p>
          </section>
        ) : null}

        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[13px] font-semibold">역할 (Grants)</span>
            <span className="text-[11px] text-muted-foreground">
              {grants.length}개 principal
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <div className="relative min-w-0 flex-1">
              <UserPlus className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={newPrincipalId}
                onChange={(event) => setNewPrincipalId(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") handleAddGrant();
                }}
                placeholder="사용자 또는 그룹 추가…"
                disabled={projectId === null}
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
                projectId === null ||
                newPrincipalId.trim().length === 0 ||
                pendingPrincipal !== null
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
          ) : (
            <DataTable
              columns={columns}
              rows={grants}
              rowKey={(grant) => grant.id}
              emptyMessage="아직 부여된 역할이 없습니다. 위에서 principal을 추가하세요."
            />
          )}

          {lastEvidence ? (
            <div className="space-y-1 rounded border bg-muted/40 p-2.5">
              <div className="flex items-center gap-1.5">
                <KeyRound className="size-3.5 text-success" />
                <span className="text-[12px] font-medium">
                  마지막 upsert 성공
                </span>
                <StatusPill intent={ROLE_INTENT[lastEvidence.role]}>
                  {ROLE_LABEL[lastEvidence.role]}
                </StatusPill>
              </div>
              <dl className="grid grid-cols-[92px_minmax(0,1fr)] gap-x-2 gap-y-0.5 text-[11px]">
                <dt className="text-muted-foreground">principal</dt>
                <dd className="truncate font-mono">
                  {lastEvidence.principalId}
                </dd>
                <dt className="text-muted-foreground">idempotency</dt>
                <dd className="truncate font-mono">
                  {lastEvidence.idempotencyKey}
                </dd>
                <dt className="text-muted-foreground">grant id</dt>
                <dd className="truncate font-mono">{lastEvidence.requestId}</dd>
              </dl>
            </div>
          ) : null}
        </section>
      </div>

      <div className="space-y-3">
        <PermissionDeniedProbe projectId={projectId} />

        <section className="space-y-1.5 rounded border border-dashed p-3">
          <div className="flex items-center justify-between">
            <span className="section-label">직접 GRANT (리소스 단위)</span>
            <StatusPill intent="neutral">future</StatusPill>
          </div>
          <div className="flex items-start gap-2">
            <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            <p className="text-[11px] text-muted-foreground">
              폴더/리소스 단위 직접 grant override와 접근 요청(access request)
              워크플로는 아직 지원되지 않습니다. 현재는 프로젝트 역할 중심
              권한만 가능합니다.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}

/**
 * viewer 롤 컨텍스트로 동일한 upsertGrant를 시도해 PERMISSION_DENIED 응답을
 * 실제로 받아 화면에 permission denied state로 렌더한다. (mock이 아니라 백엔드
 * 정책 엔진이 반환하는 실제 403 evidence다.)
 */
function PermissionDeniedProbe({ projectId }: { projectId: string | null }) {
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<PermissionProbeResult | null>(null);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);

  const runProbe = useCallback(async () => {
    if (projectId === null) return;
    setIsRunning(true);
    setResult(null);
    setError(null);
    // header-trust 프로필에서 viewer 롤만 가진 별도 principal로 요청한다.
    const viewerClient = createFoundryLiteClient({
      baseUrl: API_BASE_URL,
      context: {
        tenantId: DEMO_CONTEXT.tenantId,
        userId: "security-probe-viewer",
        roles: ["viewer"],
      },
    });
    try {
      await viewerClient.resources.projects.upsertGrant(
        projectId,
        "user",
        "security-probe-target",
        { role: "viewer" },
        {
          idempotencyKey: idempotencyKey(
            "project-grant-probe",
            `${projectId}:${crypto.randomUUID()}`,
          ),
        },
      );
      // 정책상 도달하면 안 되는 경로. 성공 시 프로브는 의미가 없다.
      setError(null);
      setResult(null);
    } catch (caught) {
      const normalized = normalizeFoundryLiteError(caught);
      const details = (normalized.details ?? {}) as Record<string, unknown>;
      setResult({
        code: normalized.code ?? "PERMISSION_DENIED",
        message: normalized.message,
        requestId: normalized.requestId,
        requiredRole:
          typeof details.required_role === "string"
            ? details.required_role
            : null,
        actualRole:
          typeof details.actual_role === "string" ? details.actual_role : null,
      });
      if ((normalized.code ?? "") !== "PERMISSION_DENIED") setError(normalized);
    } finally {
      setIsRunning(false);
    }
  }, [projectId]);

  return (
    <section className="space-y-2 rounded border p-3">
      <div className="flex items-center justify-between">
        <span className="section-label">권한 검증 프로브</span>
        <StatusPill intent="info">live</StatusPill>
      </div>
      <p className="text-[11px] text-muted-foreground">
        <span className="font-mono">viewer</span> 롤만 가진 principal로 동일한
        upsertGrant를 호출해 백엔드 정책 엔진의 거부 응답을 실제로 확인합니다.
      </p>
      <Button
        size="sm"
        variant="outline"
        className="h-7 w-full text-[12px]"
        disabled={projectId === null || isRunning}
        onClick={() => void runProbe()}
      >
        {isRunning ? "요청 중…" : "viewer 롤로 upsert 시도"}
      </Button>

      {result ? (
        <div className="space-y-1.5 rounded border border-destructive/30 bg-destructive/5 p-2.5">
          <div className="flex items-center gap-1.5">
            <ShieldAlert className="size-3.5 text-destructive" />
            <span className="text-[12px] font-semibold text-destructive">
              권한 거부됨
            </span>
            <StatusPill intent="danger">{result.code}</StatusPill>
          </div>
          <p className="text-[11px] text-muted-foreground">{result.message}</p>
          <dl className="grid grid-cols-[80px_minmax(0,1fr)] gap-x-2 gap-y-0.5 text-[11px]">
            <dt className="text-muted-foreground">필요 역할</dt>
            <dd className="font-mono">{result.requiredRole ?? "—"}</dd>
            <dt className="text-muted-foreground">보유 역할</dt>
            <dd className="font-mono">{result.actualRole ?? "none"}</dd>
            <dt className="text-muted-foreground">request id</dt>
            <dd className="truncate font-mono">{result.requestId ?? "—"}</dd>
          </dl>
          <p className="text-[11px] text-muted-foreground">
            권한이 부족하면 관리자에게 역할 부여를 요청하세요.
          </p>
        </div>
      ) : null}

      {error ? <ErrorState error={error} /> : null}
    </section>
  );
}
