import type { AipPilotApplicationBundle, TabularRow } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { ArrowLeft, ArrowUpRight, Database, GitBranch, PackageCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

type PilotState = {
  bundle: AipPilotApplicationBundle;
  rows: TabularRow[];
};

export default function PilotApplicationPage() {
  const client = useFoundryLiteClient();
  const { projectId, slug } = useParams();
  const [state, setState] = useState<PilotState | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!projectId || !slug) return;
    let isCurrent = true;
    void loadPilot(client, projectId, slug)
      .then((result) => {
        if (isCurrent) setState(result);
      })
      .catch((reason: unknown) => {
        if (isCurrent) setError(reason);
      });
    return () => {
      isCurrent = false;
    };
  }, [client, projectId, slug]);

  if (error) return <ErrorState error={error} />;
  if (!state) return <LoadingState rowCount={6} className="p-4" />;
  const seed = state.bundle.seed as Record<string, unknown> | undefined;
  const branch = state.bundle.ontologyBranch as Record<string, unknown> | undefined;
  return (
    <div className="space-y-4 p-4">
      <PageHeader
        title={state.bundle.applicationName}
        description="AI FDE가 만든 branch-first OSDK 애플리케이션 미리보기"
        meta={<StatusPill intent="info">{state.bundle.status}</StatusPill>}
        actions={
          <div className="flex gap-2">
            <Button asChild variant="outline" size="sm">
              <Link to="/aip/fde"><ArrowLeft /> AI FDE로 돌아가기</Link>
            </Button>
            <Button asChild size="sm">
              <Link to={state.bundle.operatingPath}><ArrowUpRight /> 운영 앱 열기</Link>
            </Button>
          </div>
        }
      />
      <div className="grid gap-3 md:grid-cols-3">
        <EvidenceCard icon={Database} title="Seed Dataset" value={String(seed?.datasetRef ?? "-")} />
        <EvidenceCard icon={GitBranch} title="Ontology working branch" value={String(branch?.id ?? "-")} />
        <EvidenceCard icon={PackageCheck} title="Generated files" value={`${Object.keys(state.bundle.reactFiles).length} files + CI`} />
      </div>
      <div className="rounded border bg-card p-3">
        <div className="text-[12px] font-medium">Seed data preview</div>
        {state.rows.length === 0 ? (
          <EmptyState icon={Database} title="표시할 seed row가 없습니다" description="생성 계획의 seed 데이터를 확인하세요." />
        ) : (
          <pre className="mt-3 overflow-auto rounded bg-muted/30 p-3 font-mono text-[11px]">
            {JSON.stringify(state.rows, null, 2)}
          </pre>
        )}
      </div>
      <div className="rounded border border-warning/30 bg-warning/5 p-3 text-[11px] leading-relaxed">
        이 화면은 생성 직후의 안전한 preview입니다. Ontology proposal을 사람이 검토·활성화하기 전에는 production 객체나 Action을 사용하지 않습니다.
      </div>
    </div>
  );
}

async function loadPilot(
  client: ReturnType<typeof useFoundryLiteClient>,
  projectId: string,
  slug: string,
): Promise<PilotState> {
  const resources = await client.resources.items.search({ projectId });
  const resource = resources.items.find(
    (item) => item.resourceType === "pilot_application" && item.metadata.slug === slug,
  );
  if (!resource) throw new Error("Pilot application resource was not found");
  const bundle = await client.aip.pilot.get(resource.rid);
  const datasetRef = String((bundle.seed as Record<string, unknown> | undefined)?.datasetRef ?? "");
  const [namespace, ...nameParts] = datasetRef.split(".");
  const rows = namespace && nameParts.length > 0
    ? await client.datasets.preview(namespace, nameParts.join("."), { limit: 25 })
    : [];
  return { bundle, rows };
}

function EvidenceCard({
  icon: Icon,
  title,
  value,
}: {
  icon: typeof Database;
  title: string;
  value: string;
}) {
  return (
    <div className="rounded border bg-card p-3">
      <Icon className="size-4 text-primary" />
      <div className="mt-2 text-[10px] uppercase tracking-wide text-muted-foreground">{title}</div>
      <div className="mt-1 break-all font-mono text-[11px]">{value}</div>
    </div>
  );
}
