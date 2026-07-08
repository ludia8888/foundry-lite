import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { Download, Package, Plus, Rocket } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DataTable } from "@/components/shared/DataTable";
import type { DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { CopyField } from "./CopyField";
import {
  formatTimestamp,
  statusIntent,
  toDownloadToken,
  type OsdkChannelRow,
  type OsdkDownloadToken,
  type OsdkSdkVersionRow,
} from "./developer-model";
import {
  useSdkChannels,
  useSdkCompatibilityWindows,
  useSdkInstallMetadata,
  useSdkVersions,
} from "./use-developer-queries";

interface SdkVersionsTabProps {
  appId: string;
}

const CHANNELS = ["stable", "beta", "latest"] as const;
const LANGUAGES = [
  { value: "typescript", label: "TypeScript" },
  { value: "python", label: "Python" },
] as const;

/**
 * SDK 버전 탭.
 * sdkVersions.create/list/promote + channels + compatibilityWindows +
 * installMetadata + artifact download token.
 */
export function SdkVersionsTab({ appId }: SdkVersionsTabProps) {
  const client = useFoundryLiteClient();
  const versionsQuery = useSdkVersions(appId);
  const channelsQuery = useSdkChannels(appId);
  const windowsQuery = useSdkCompatibilityWindows(appId);
  const installQuery = useSdkInstallMetadata(appId);

  const [language, setLanguage] = useState<string>("typescript");
  const [promoteChannel, setPromoteChannel] = useState<Record<string, string>>(
    {},
  );
  const [lastKey, setLastKey] = useState<string | null>(null);
  const [downloadToken, setDownloadToken] = useState<OsdkDownloadToken | null>(
    null,
  );

  const createVersion = useFoundryLiteMutation(
    ({ lang, key }: { lang: string; key: string }) =>
      client.developerConsole.sdkVersions.create(
        appId,
        { language: lang, requestedBump: "minor" },
        { idempotencyKey: key },
      ),
    { lockKey: ({ lang }) => `developer:sdk-version:create:${appId}:${lang}` },
  );

  const promoteVersion = useFoundryLiteMutation(
    ({
      versionId,
      channel,
      key,
    }: {
      versionId: string;
      channel: string;
      key: string;
    }) =>
      client.developerConsole.sdkVersions.promote(appId, versionId, channel, {
        idempotencyKey: key,
      }),
    {
      lockKey: ({ versionId, channel }) =>
        `developer:sdk-version:promote:${versionId}:${channel}`,
    },
  );

  const issueDownloadToken = useFoundryLiteMutation(
    ({ versionId, key }: { versionId: string; key: string }) =>
      client.developerConsole.sdkVersions.downloadToken(
        appId,
        versionId,
        languageArtifactKind(),
        { ttlSeconds: 600 },
        { idempotencyKey: key },
      ),
    {
      lockKey: ({ versionId }) =>
        `developer:sdk-version:download-token:${versionId}`,
    },
  );

  function languageArtifactKind(): string {
    return language;
  }

  const reloadAll = async () => {
    await Promise.all([
      versionsQuery.reload(),
      channelsQuery.reload(),
      windowsQuery.reload(),
      installQuery.reload(),
    ]);
  };

  const handleCreate = async () => {
    const key = idempotencyKey("osdk_sdk_version", `${appId}:${language}`);
    setLastKey(key);
    const created = await createVersion.execute({ lang: language, key });
    if (created) {
      toast.success("SDK 버전이 생성되었습니다");
      await reloadAll();
    }
  };

  const handlePromote = async (row: OsdkSdkVersionRow) => {
    const channel = promoteChannel[row.id] ?? "stable";
    const key = idempotencyKey("osdk_sdk_promote", `${row.id}:${channel}`);
    setLastKey(key);
    const promoted = await promoteVersion.execute({
      versionId: row.id,
      channel,
      key,
    });
    if (promoted) {
      toast.success(`${row.version} → ${channel} 채널로 promote됨`);
      await reloadAll();
    }
  };

  const handleDownloadToken = async (row: OsdkSdkVersionRow) => {
    const key = idempotencyKey("osdk_download_token", row.id);
    setLastKey(key);
    setDownloadToken(null);
    const result = await issueDownloadToken.execute({
      versionId: row.id,
      key,
    });
    if (result) {
      setDownloadToken(toDownloadToken(result));
      toast.success("아티팩트 다운로드 토큰이 발급되었습니다");
    }
  };

  const channelByVersionId = new Map<string, OsdkChannelRow[]>();
  for (const channel of channelsQuery.data ?? []) {
    const list = channelByVersionId.get(channel.sdkVersionId) ?? [];
    list.push(channel);
    channelByVersionId.set(channel.sdkVersionId, list);
  }

  const columns: readonly DataTableColumn<OsdkSdkVersionRow>[] = [
    {
      key: "version",
      header: "버전",
      isMono: true,
      render: (row) => row.version,
    },
    {
      key: "language",
      header: "언어",
      render: (row) => <StatusPill intent="neutral">{row.language}</StatusPill>,
    },
    {
      key: "package",
      header: "패키지",
      isMono: true,
      render: (row) => row.packageName ?? "-",
    },
    {
      key: "release",
      header: "릴리스",
      render: (row) => (
        <StatusPill intent={statusIntent(row.releaseStatus)}>
          {row.releaseStatus}
        </StatusPill>
      ),
    },
    {
      key: "compatibility",
      header: "호환성",
      render: (row) => (
        <StatusPill intent={statusIntent(row.compatibilityStatus)}>
          {row.compatibilityStatus}
        </StatusPill>
      ),
    },
    {
      key: "channels",
      header: "채널",
      render: (row) => {
        const assigned = channelByVersionId.get(row.id) ?? [];
        return assigned.length > 0 ? (
          <div className="flex gap-1">
            {assigned.map((channel) => (
              <StatusPill key={channel.id} intent="success">
                {channel.channel}
              </StatusPill>
            ))}
          </div>
        ) : (
          <span className="text-muted-foreground">미배정</span>
        );
      },
    },
    {
      key: "promote",
      header: "promote",
      render: (row) => (
        <div className="flex items-center gap-1.5">
          <Select
            value={promoteChannel[row.id] ?? "stable"}
            onValueChange={(value) =>
              setPromoteChannel((prev) => ({ ...prev, [row.id]: value }))
            }
          >
            <SelectTrigger size="sm" className="h-7 w-24">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CHANNELS.map((channel) => (
                <SelectItem key={channel} value={channel}>
                  {channel}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="xs"
            onClick={() => void handlePromote(row)}
            disabled={promoteVersion.isRunning}
          >
            <Rocket className="size-3" /> promote
          </Button>
        </div>
      ),
    },
    {
      key: "download",
      header: "",
      className: "text-right",
      render: (row) => (
        <Button
          variant="ghost"
          size="xs"
          onClick={() => void handleDownloadToken(row)}
          disabled={issueDownloadToken.isRunning}
        >
          <Download className="size-3" /> 토큰
        </Button>
      ),
    },
  ];

  const versions = versionsQuery.data ?? [];
  const windows = windowsQuery.data ?? [];
  const install = installQuery.data;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="section-label">SDK 버전 ({versions.length})</div>
        <div className="flex items-center gap-1.5">
          <Select value={language} onValueChange={setLanguage}>
            <SelectTrigger size="sm" className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LANGUAGES.map((lang) => (
                <SelectItem key={lang.value} value={lang.value}>
                  {lang.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            onClick={() => void handleCreate()}
            disabled={createVersion.isRunning}
          >
            <Plus className="size-3.5" /> 버전 생성
          </Button>
        </div>
      </div>

      {versionsQuery.error ? (
        <ErrorState
          error={versionsQuery.error}
          onRetry={() => void versionsQuery.reload()}
        />
      ) : null}

      {versionsQuery.isLoading ? (
        <LoadingState rowCount={3} />
      ) : versions.length === 0 ? (
        <EmptyState
          icon={Package}
          title="아직 생성된 SDK가 없습니다"
          description="언어를 선택하고 버전을 생성하면 온톨로지 scope에 맞춰 SDK 아티팩트가 만들어집니다."
          action={
            <Button
              size="sm"
              onClick={() => void handleCreate()}
              disabled={createVersion.isRunning}
            >
              <Plus className="size-3.5" /> 첫 버전 생성
            </Button>
          }
        />
      ) : (
        <DataTable columns={columns} rows={versions} rowKey={(row) => row.id} />
      )}

      {createVersion.error ? <ErrorState error={createVersion.error} /> : null}
      {promoteVersion.error ? (
        <ErrorState error={promoteVersion.error} />
      ) : null}
      {issueDownloadToken.error ? (
        <ErrorState error={issueDownloadToken.error} />
      ) : null}

      {downloadToken ? (
        <div className="space-y-2 rounded border border-primary/40 bg-primary/5 p-3">
          <div className="section-label text-primary">
            아티팩트 다운로드 토큰
          </div>
          <CopyField
            label="download token"
            value={downloadToken.downloadToken}
          />
          <div className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
            <span>artifact_id={downloadToken.artifactId}</span>
            <span>expires_at={formatTimestamp(downloadToken.expiresAt)}</span>
          </div>
        </div>
      ) : null}

      {lastKey ? (
        <div className="font-mono text-[11px] text-muted-foreground">
          idempotency_key={lastKey}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <div className="section-label">호환성 윈도우 ({windows.length})</div>
          {windows.length === 0 ? (
            <div className="rounded border border-dashed px-3 py-4 text-center text-xs text-muted-foreground">
              등록된 호환성 윈도우가 없습니다.
            </div>
          ) : (
            <div className="overflow-hidden rounded border bg-card">
              <table className="w-full border-collapse text-[12px]">
                <thead>
                  <tr className="border-b">
                    <th className="section-label h-8 px-3 text-left">from</th>
                    <th className="section-label h-8 px-3 text-left">to</th>
                    <th className="section-label h-8 px-3 text-left">
                      supported until
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {windows.map((window) => (
                    <tr key={window.id} className="border-b last:border-b-0">
                      <td className="h-8 px-3 font-mono text-[11px]">
                        {window.fromVersionId.slice(-8)}
                      </td>
                      <td className="h-8 px-3 font-mono text-[11px]">
                        {window.toVersionId.slice(-8)}
                      </td>
                      <td className="h-8 px-3 text-muted-foreground">
                        {formatTimestamp(window.supportedUntil)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="space-y-2">
          <div className="section-label">install 메타데이터 증거</div>
          {install ? (
            <div className="space-y-1.5 rounded border bg-card p-3 font-mono text-[11px]">
              <div className="flex justify-between">
                <span className="text-muted-foreground">sdk_versions</span>
                <span>{install.sdkVersions.length}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">channels</span>
                <span>{install.channels.length}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">
                  compatibility_windows
                </span>
                <span>{install.compatibilityWindows.length}</span>
              </div>
              {install.sdkVersions[0]?.artifactHash ? (
                <div className="border-t pt-1.5">
                  <div className="text-muted-foreground">latest artifact</div>
                  <div className="truncate">
                    {install.sdkVersions[0].artifactHash}
                  </div>
                </div>
              ) : null}
            </div>
          ) : installQuery.isLoading ? (
            <LoadingState rowCount={2} />
          ) : (
            <div className="rounded border border-dashed px-3 py-4 text-center text-xs text-muted-foreground">
              메타데이터 없음
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
