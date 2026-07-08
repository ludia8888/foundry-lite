import { ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { capabilityLabel } from "../source-model";
import type { ConnectionMode } from "./ConnectionMethodStep";
import { WizardField, WizardStepFooter } from "./WizardFields";

/** 자격 증명 + 네트워크 정책 폼 (Palantir add network egress policy 구조). */
export function CredentialNetworkStep({
  identitySection,
  connectionMode,
  hasCredential,
  onHasCredentialChange,
  authScheme,
  onAuthSchemeChange,
  secretValue,
  onSecretValueChange,
  credentialName,
  hasNetworkPolicy,
  onHasNetworkPolicyChange,
  allowedHostsText,
  onAllowedHostsChange,
  policyName,
  agentId,
  canContinue,
  onBack,
  onContinue,
}: {
  identitySection?: ReactNode;
  connectionMode: ConnectionMode;
  hasCredential: boolean;
  onHasCredentialChange: (value: boolean) => void;
  authScheme: string;
  onAuthSchemeChange: (value: string) => void;
  secretValue: string;
  onSecretValueChange: (value: string) => void;
  credentialName: string;
  hasNetworkPolicy: boolean;
  onHasNetworkPolicyChange: (value: boolean) => void;
  allowedHostsText: string;
  onAllowedHostsChange: (value: string) => void;
  policyName: string;
  agentId: string;
  canContinue: boolean;
  onBack: () => void;
  onContinue: () => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-semibold">자격 증명 & 네트워크 정책</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          비밀 값은 vault에 저장되며 화면에는 다시 표시되지 않습니다. 정책은
          소스에 적용되어야 트래픽이 허용됩니다.
        </p>
      </div>
      {identitySection}
      <section className="space-y-3 rounded border bg-card p-4">
        <label className="flex items-center gap-2 text-xs font-semibold">
          <Checkbox
            checked={hasCredential}
            onCheckedChange={(checked) =>
              onHasCredentialChange(checked === true)
            }
          />
          자격 증명 저장
          <ShieldCheck className="size-3.5 text-success" />
        </label>
        {hasCredential ? (
          <div className="grid gap-3 md:grid-cols-2">
            <WizardField
              label="자격 증명 이름"
              helper="소스 이름에서 자동 생성됩니다."
            >
              <Input
                value={credentialName}
                readOnly
                className="h-8 bg-muted/50 font-mono text-xs"
              />
            </WizardField>
            <WizardField label="인증 방식">
              <Select value={authScheme} onValueChange={onAuthSchemeChange}>
                <SelectTrigger size="sm" className="w-full text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="bearer">Bearer 토큰</SelectItem>
                  <SelectItem value="basic_auth">Basic 인증</SelectItem>
                  <SelectItem value="api_key">API 키</SelectItem>
                </SelectContent>
              </Select>
            </WizardField>
            <WizardField
              label="비밀 값"
              helper="저장 후에는 ***REDACTED*** 참조로만 노출됩니다."
              className="md:col-span-2"
            >
              <Input
                type="password"
                value={secretValue}
                onChange={(event) => onSecretValueChange(event.target.value)}
                placeholder="토큰 또는 비밀번호"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
          </div>
        ) : null}
      </section>
      <section className="space-y-3 rounded border bg-card p-4">
        <label className="flex items-center gap-2 text-xs font-semibold">
          <Checkbox
            checked={hasNetworkPolicy}
            onCheckedChange={(checked) =>
              onHasNetworkPolicyChange(checked === true)
            }
          />
          네트워크 egress 정책 생성
        </label>
        {hasNetworkPolicy ? (
          <div className="grid gap-3 md:grid-cols-2">
            <WizardField label="정책 이름">
              <Input
                value={policyName}
                readOnly
                className="h-8 bg-muted/50 font-mono text-xs"
              />
            </WizardField>
            <WizardField
              label="허용 주소"
              helper="이 정책이 허용할 호스트를 쉼표로 구분해 입력합니다."
            >
              <Input
                value={allowedHostsText}
                onChange={(event) => onAllowedHostsChange(event.target.value)}
                placeholder="api.example.com, db.internal"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
            {connectionMode === "agent_proxy" ? (
              <WizardField
                label="에이전트"
                helper="에이전트 경유 모드에서는 아래 에이전트가 함께 등록됩니다."
                className="md:col-span-2"
              >
                <Input
                  value={agentId}
                  readOnly
                  className="h-8 bg-muted/50 font-mono text-xs"
                />
              </WizardField>
            ) : null}
          </div>
        ) : null}
      </section>
      <WizardStepFooter
        left={
          <Button variant="outline" size="sm" onClick={onBack}>
            이전
          </Button>
        }
        right={
          <Button size="sm" disabled={!canContinue} onClick={onContinue}>
            다음
          </Button>
        }
      />
    </div>
  );
}

/** 관리형 동기화 설정 + 스케줄 + 타입별 data-plane 구성. */
export function SyncConfigStep({
  sourceType,
  capabilities,
  capability,
  onCapabilityChange,
  syncName,
  datasetRef,
  datasetRefError,
  onDatasetRefChange,
  syncMode,
  onSyncModeChange,
  scheduleMode,
  onScheduleModeChange,
  everySecondsText,
  onEverySecondsChange,
  typeConfigSection,
  shouldStartRun,
  onShouldStartRunChange,
  isRunSupported,
  canContinue,
  onBack,
  onContinue,
}: {
  sourceType: string;
  capabilities: readonly string[];
  capability: string;
  onCapabilityChange: (value: string) => void;
  syncName: string;
  datasetRef: string;
  datasetRefError: string | null;
  onDatasetRefChange: (value: string) => void;
  syncMode: string;
  onSyncModeChange: (value: string) => void;
  scheduleMode: string;
  onScheduleModeChange: (value: string) => void;
  everySecondsText: string;
  onEverySecondsChange: (value: string) => void;
  typeConfigSection: ReactNode;
  shouldStartRun: boolean;
  onShouldStartRunChange: (value: boolean) => void;
  isRunSupported: boolean;
  canContinue: boolean;
  onBack: () => void;
  onContinue: () => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-semibold">동기화 설정</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          반복 실행할 관리형 동기화와 대상 데이터셋을 정의합니다.
        </p>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <WizardField label="동기화 이름">
          <Input
            value={syncName}
            readOnly
            className="h-8 bg-muted/50 font-mono text-xs"
          />
        </WizardField>
        <WizardField label="capability">
          <Select value={capability} onValueChange={onCapabilityChange}>
            <SelectTrigger size="sm" className="w-full text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {capabilities.map((item) => (
                <SelectItem key={item} value={item}>
                  {capabilityLabel(item)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </WizardField>
        <WizardField
          label="대상 데이터셋"
          helper="namespace.name 형식"
          error={datasetRefError}
        >
          <Input
            value={datasetRef}
            onChange={(event) => onDatasetRefChange(event.target.value)}
            placeholder="demo.orders_synced"
            className="h-8 font-mono text-xs"
          />
        </WizardField>
        <WizardField label="트랜잭션 모드">
          <Select value={syncMode} onValueChange={onSyncModeChange}>
            <SelectTrigger size="sm" className="w-full text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="SNAPSHOT">SNAPSHOT (전체 교체)</SelectItem>
              <SelectItem value="APPEND">APPEND (증분 추가)</SelectItem>
            </SelectContent>
          </Select>
        </WizardField>
        <WizardField label="스케줄">
          <Select value={scheduleMode} onValueChange={onScheduleModeChange}>
            <SelectTrigger size="sm" className="w-full text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="manual">수동 실행</SelectItem>
              <SelectItem value="scheduled">주기 실행</SelectItem>
            </SelectContent>
          </Select>
        </WizardField>
        {scheduleMode === "scheduled" ? (
          <WizardField label="실행 주기 (초)">
            <Input
              value={everySecondsText}
              onChange={(event) => onEverySecondsChange(event.target.value)}
              placeholder="3600"
              inputMode="numeric"
              className="h-8 font-mono text-xs"
            />
          </WizardField>
        ) : null}
      </div>
      {typeConfigSection}
      <section className="space-y-2 rounded border bg-card p-4">
        <label className="flex items-center gap-2 text-xs font-semibold">
          <Checkbox
            checked={shouldStartRun}
            onCheckedChange={(checked) =>
              onShouldStartRunChange(checked === true)
            }
            disabled={!isRunSupported}
          />
          생성 직후 첫 run 시작
        </label>
        {!isRunSupported ? (
          <p className="text-[11px] text-muted-foreground">
            {sourceType} 타입의 관리형 run data-plane은 아직 future 범위입니다.
            동기화 정의까지만 생성합니다.
          </p>
        ) : null}
      </section>
      <WizardStepFooter
        left={
          <Button variant="outline" size="sm" onClick={onBack}>
            이전
          </Button>
        }
        right={
          <Button size="sm" disabled={!canContinue} onClick={onContinue}>
            다음
          </Button>
        }
      />
    </div>
  );
}
