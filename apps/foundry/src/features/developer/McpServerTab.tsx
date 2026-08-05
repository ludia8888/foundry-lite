import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { Bot, RefreshCw, ServerCog } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import { CopyField } from "./CopyField";

type Json = Record<string, unknown>;

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function lines(value: string): string[] {
  return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
}

export function McpServerTab({ appId }: { appId: string }) {
  const client = useFoundryLiteClient();
  const [server, setServer] = useState<Json | null>(null);
  const [hub, setHub] = useState<Json[]>([]);
  const [status, setStatus] = useState<"enabled" | "disabled">("disabled");
  const [description, setDescription] = useState("Ontology tools for governed external AI agents.");
  const [origins, setOrigins] = useState("");
  const [loadError, setLoadError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    const [serverResult, hubResult] = await Promise.allSettled([
      client.developerConsole.mcpServers.get(appId),
      client.developerConsole.mcpServers.list(),
    ]);
    if (hubResult.status === "fulfilled") setHub(hubResult.value);
    else setLoadError(hubResult.reason);
    if (serverResult.status === "fulfilled") {
      const current = serverResult.value;
      setServer(current);
      setStatus(text(current.status) === "enabled" ? "enabled" : "disabled");
      setDescription(text(current.description_markdown ?? current.descriptionMarkdown));
      const allowedOrigins = current.allowed_origins ?? current.allowedOrigins;
      setOrigins(Array.isArray(allowedOrigins) ? allowedOrigins.join("\n") : "");
    } else {
      setServer(null);
    }
  }, [appId, client]);

  useEffect(() => {
    void load();
  }, [load]);

  const configure = useFoundryLiteMutation(
    ({ key }: { key: string }) =>
      client.developerConsole.mcpServers.configure(
        appId,
        { status, descriptionMarkdown: description.trim(), allowedOrigins: lines(origins) },
        { idempotencyKey: key },
      ),
    { lockKey: () => `developer:mcp-server:${appId}` },
  );

  const save = async () => {
    const key = idempotencyKey("ontology_mcp_server_configure", `${appId}:${status}`);
    const result = await configure.execute({ key });
    if (result) {
      toast.success(status === "enabled" ? "Ontology MCP 서버가 공개되었습니다" : "Ontology MCP 서버가 중지되었습니다");
      await load();
    }
  };

  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const endpoint = `${origin}/mcp/ontology/${appId}`;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <ServerCog className="size-4" />
            <h3 className="text-sm font-semibold">Ontology MCP 서버</h3>
            <StatusPill intent={server?.status === "enabled" ? "success" : "neutral"}>
              {server ? text(server.status) : "not configured"}
            </StatusPill>
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            이 앱에 허용된 객체·Action·query function만 ChatGPT 같은 외부 에이전트에 공개합니다.
          </p>
        </div>
        <Button variant="ghost" size="xs" onClick={() => void load()}>
          <RefreshCw className="size-3" /> 새로고침
        </Button>
      </div>

      <CopyField label="Streamable HTTP endpoint" value={endpoint} />
      <div className="grid gap-3 md:grid-cols-2">
        <CopyField label="Protected resource metadata" value={`${origin}/.well-known/oauth-protected-resource/mcp/ontology/${appId}`} />
        <CopyField label="Authorization server metadata" value={`${origin}/.well-known/oauth-authorization-server`} />
      </div>

      <div className="space-y-3 rounded border bg-card p-3">
        <div className="grid grid-cols-2 gap-2">
          {(["enabled", "disabled"] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setStatus(option)}
              className={`rounded border px-3 py-2 text-xs ${status === option ? "border-primary bg-primary/10 text-primary" : "border-border"}`}
            >
              {option === "enabled" ? "외부 에이전트에 공개" : "공개 중지"}
            </button>
          ))}
        </div>
        <div className="space-y-1">
          <Label className="section-label">에이전트용 설명</Label>
          <Textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} />
        </div>
        <div className="space-y-1">
          <Label className="section-label">허용 Origin</Label>
          <Textarea
            value={origins}
            onChange={(event) => setOrigins(event.target.value)}
            placeholder="https://chatgpt.com\nhttps://your-agent.example"
            rows={3}
            className="font-mono text-xs"
          />
          <p className="text-[10px] text-muted-foreground">브라우저 기반 MCP 요청은 이 목록과 정확히 일치해야 합니다.</p>
        </div>
        <div className="flex justify-end">
          <Button onClick={() => void save()} disabled={!description.trim() || configure.isRunning}>
            설정 저장
          </Button>
        </div>
      </div>

      {loadError ? <ErrorState error={loadError} onRetry={() => void load()} /> : null}
      {configure.error ? <ErrorState error={configure.error} /> : null}

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Bot className="size-4" />
          <div className="section-label">MCP Hub · 이 tenant의 공개 서버 ({hub.length})</div>
        </div>
        {hub.length === 0 ? (
          <div className="rounded border border-dashed p-4 text-center text-xs text-muted-foreground">
            공개된 MCP 서버가 없습니다.
          </div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
            {hub.map((item) => (
              <div key={text(item.applicationId)} className="rounded border bg-card p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{text(item.displayName)}</span>
                  <StatusPill intent="success">{text(item.status)}</StatusPill>
                </div>
                <p className="mt-1 text-[11px] text-muted-foreground">{text(item.descriptionMarkdown)}</p>
                <div className="mt-2 font-mono text-[10px] text-muted-foreground">
                  resources={String(item.resourceCount ?? 0)} · {Array.isArray(item.authModes) ? item.authModes.join(" · ") : ""}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
