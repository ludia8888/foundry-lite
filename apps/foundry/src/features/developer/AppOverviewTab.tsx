import { StatusPill } from "@/components/shared/StatusPill";

import { CopyField } from "./CopyField";
import { ResourceScopeEditor } from "./ResourceScopeEditor";
import {
  formatTimestamp,
  statusIntent,
  type OsdkApplicationRow,
  type OsdkResourceRow,
} from "./developer-model";

interface AppOverviewTabProps {
  application: OsdkApplicationRow;
  resources: OsdkResourceRow[];
  onResourcesSaved: () => void;
}

/** 앱 개요 탭: 애플리케이션 상세 + 리소스 권한 편집기. */
export function AppOverviewTab({
  application,
  resources,
  onResourcesSaved,
}: AppOverviewTabProps) {
  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded border bg-card p-4">
        <div className="section-label">애플리케이션 상세</div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-0.5">
            <div className="section-label">표시 이름</div>
            <div className="text-[13px]">{application.displayName}</div>
          </div>
          <div className="space-y-0.5">
            <div className="section-label">상태</div>
            <StatusPill intent={statusIntent(application.status)}>
              {application.status}
            </StatusPill>
          </div>
          <CopyField label="API name" value={application.appApiName} />
          <CopyField label="application id" value={application.id} />
          <div className="space-y-0.5">
            <div className="section-label">소유자</div>
            <div className="font-mono text-[11px]">
              {application.ownerUserId ?? "-"}
            </div>
          </div>
          <div className="space-y-0.5">
            <div className="section-label">생성 / 수정</div>
            <div className="text-[11px] text-muted-foreground">
              {formatTimestamp(application.createdAt)} ·{" "}
              {formatTimestamp(application.updatedAt)}
            </div>
          </div>
        </div>
      </section>

      <section className="rounded border bg-card p-4">
        <ResourceScopeEditor
          appId={application.id}
          resources={resources}
          onSaved={onResourcesSaved}
        />
      </section>
    </div>
  );
}
