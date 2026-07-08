import type { SourceNetworkPolicy } from "@foundry-lite/sdk";
import { ShieldCheck } from "lucide-react";

import type { DataTableColumn } from "@/components/shared/DataTable";
import { DataTable } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";

import { formatTimestamp, statusIntent, statusLabel } from "./source-model";
import { useNetworkPolicies } from "./use-source-queries";

function allowedHostsSummary(policy: SourceNetworkPolicy): string {
  const hosts = policy.allowedHosts["hosts"] ?? policy.allowedHosts;
  if (Array.isArray(hosts)) {
    return hosts.length > 0 ? hosts.join(", ") : "제한 없음";
  }
  const entries = Object.values(policy.allowedHosts);
  return entries.length > 0 ? entries.map(String).join(", ") : "제한 없음";
}

const POLICY_COLUMNS: readonly DataTableColumn<SourceNetworkPolicy>[] = [
  {
    key: "policyName",
    header: "정책",
    isMono: true,
    render: (policy) => policy.policyName,
  },
  {
    key: "displayName",
    header: "이름",
    render: (policy) => policy.displayName,
  },
  {
    key: "mode",
    header: "모드",
    isMono: true,
    render: (policy) => policy.mode,
  },
  {
    key: "agent",
    header: "에이전트",
    isMono: true,
    render: (policy) => policy.agentId ?? "—",
  },
  {
    key: "hosts",
    header: "허용 호스트",
    isMono: true,
    className: "max-w-64 truncate",
    render: (policy) => allowedHostsSummary(policy),
  },
  {
    key: "status",
    header: "상태",
    render: (policy) => (
      <StatusPill intent={statusIntent(policy.status)}>
        {statusLabel(policy.status)}
      </StatusPill>
    ),
  },
  {
    key: "createdAt",
    header: "생성",
    isMono: true,
    render: (policy) => formatTimestamp(policy.createdAt),
  },
];

/** 네트워크 정책 탭: egress 정책 목록 (생성은 새 소스 위저드에서). */
export function NetworkPolicyPanel() {
  const policiesQuery = useNetworkPolicies();

  if (policiesQuery.error) {
    return (
      <div className="p-4">
        <ErrorState
          error={policiesQuery.error}
          onRetry={() => void policiesQuery.reload()}
        />
      </div>
    );
  }
  if (policiesQuery.isLoading && !policiesQuery.data) {
    return <LoadingState rowCount={4} className="p-4" />;
  }

  const policies = policiesQuery.data ?? [];

  return (
    <div className="space-y-3 p-4">
      <div>
        <div className="section-label">네트워크 egress 정책</div>
        <p className="mt-1 text-xs text-muted-foreground">
          정책은 소스에 적용되어야 트래픽이 허용됩니다. 새 정책은 새 소스
          위저드의 자격 증명 & 네트워크 단계에서 만듭니다.
        </p>
      </div>
      {policies.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title="네트워크 정책이 없습니다"
          description="새 소스 위저드에서 egress 정책을 함께 생성할 수 있습니다."
        />
      ) : (
        <DataTable
          columns={POLICY_COLUMNS}
          rows={policies}
          rowKey={(policy) => policy.policyName}
        />
      )}
    </div>
  );
}
