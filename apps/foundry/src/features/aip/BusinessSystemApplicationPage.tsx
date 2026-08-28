import type { AipPilotOperatingApplicationBundle } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteProvidedOntologyWorkspaceShell,
} from "@foundry-lite/sdk/react";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { migrateAppDefinition } from "@/features/workshop/lib/app-model";
import { RuntimeMode } from "@/features/workshop/runtime/RuntimeMode";

export default function BusinessSystemApplicationPage() {
  const client = useFoundryLiteClient();
  const { applicationId = "" } = useParams();
  const [bundle, setBundle] = useState<AipPilotOperatingApplicationBundle | null>(null);
  const [error, setError] = useState<unknown>(null);
  const workspace = useFoundryLiteProvidedOntologyWorkspaceShell({
    key: ["business-system-workshop", applicationId],
  });

  useEffect(() => {
    let isCurrent = true;
    void client.aip.pilot.getOperating(applicationId)
      .then((result) => isCurrent && setBundle(result))
      .catch((reason: unknown) => isCurrent && setError(reason));
    return () => { isCurrent = false; };
  }, [applicationId, client]);

  const definition = useMemo(
    () => migrateAppDefinition(bundle?.businessSystemDefinition.experience.workshopApp),
    [bundle],
  );

  if (error) return <ErrorState error={error} />;
  if (!bundle || (workspace.isLoading && !workspace.catalog)) {
    return <LoadingState rowCount={8} className="min-h-screen p-8" />;
  }
  if (bundle.operatingApplication.status !== "operating") {
    return <WorkshopReleaseGate bundle={bundle} />;
  }
  if (workspace.error && !workspace.catalog) return <ErrorState error={workspace.error} />;
  if (!definition) {
    return <ErrorState error={new Error("Workshop 화면 정의를 확인할 수 없어 앱을 열지 않았습니다.")} />;
  }
  return (
    <div className="h-screen min-h-0" data-business-system-runtime="workshop">
      <RuntimeMode
        definition={definition}
        objectViewsByApiName={workspace.objectViewsByApiName}
        actionViews={workspace.actionViews}
        applicationId={applicationId}
      />
    </div>
  );
}

function WorkshopReleaseGate({ bundle }: { bundle: AipPilotOperatingApplicationBundle }) {
  return (
    <main className="grid min-h-screen place-items-center bg-[#f6f8fa] p-6">
      <section className="w-full max-w-2xl rounded border border-[#d5dce1] bg-white p-6 shadow-sm">
        <div className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded bg-[#7548c9] text-xs font-bold text-white">W</span>
          <h1 className="text-base font-semibold text-[#1c2127]">{bundle.applicationName}</h1>
          <StatusPill intent="warning">게시 전 확인</StatusPill>
        </div>
        <p className="mt-4 text-sm leading-6 text-[#5f6b7c]">
          Workshop 화면은 준비됐지만 데이터, 권한, Ontology 승인 조건이 모두 충족된 뒤에만 열립니다.
        </p>
        <ul className="mt-4 space-y-2 text-sm text-[#404854]">
          {bundle.operatingApplication.blockers.map((blocker) => <li key={blocker.code}>• {blocker.message}</li>)}
        </ul>
      </section>
    </main>
  );
}
