import type {
  FoundryLiteOntologyBranchMutationsState,
  FoundryLiteOntologyBranchState,
} from "@foundry-lite/sdk/react";
import { Download, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { parseDefinitionText } from "../lib/object-type-draft";
import { DraftPanel } from "./DraftPanel";

interface AdvancedSectionProps {
  yamlText: string;
  isDraftDirty: boolean;
  branchDetail: FoundryLiteOntologyBranchState;
  updateMutation: FoundryLiteOntologyBranchMutationsState["update"];
  activeVersionNumber: number | null;
  onYamlChange: (text: string) => void;
  onSaveToBranch: (yamlText: string) => void;
  onOntologyChanged: () => void;
}

/**
 * Advanced 섹션 (dfe64831 대응): Export/Import ontology 카드 + JSON 편집기.
 * YAML(=JSON) 편집기는 여기로 강등된다. 폼 편집이 항상 우선.
 */
export function AdvancedSection({
  yamlText,
  isDraftDirty,
  branchDetail,
  updateMutation,
  activeVersionNumber,
  onYamlChange,
  onSaveToBranch,
  onOntologyChanged,
}: AdvancedSectionProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [importText, setImportText] = useState("");
  const [importError, setImportError] = useState<string | null>(null);

  const handleExport = () => {
    const blob = new Blob([yamlText], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "ontology.json";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const applyImport = (text: string) => {
    if (parseDefinitionText(text) === null) {
      setImportError(
        "JSON 정의 형식이 아닙니다. objectTypes 등을 포함한 온톨로지 JSON을 붙여넣으세요.",
      );
      return;
    }
    setImportError(null);
    onYamlChange(text);
  };

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    void file.text().then((text) => {
      setImportText(text);
      applyImport(text);
    });
    event.target.value = "";
  };

  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded border bg-[#f5f9fe] p-4">
          <Upload className="mx-auto my-6 size-7 text-primary" />
          <div className="text-[13px] font-semibold">Export ontology</div>
          <p className="mt-1 text-xs text-muted-foreground">
            내보낸 온톨로지에는 저장하지 않은 변경 사항이 포함됩니다.
          </p>
          <Button
            size="sm"
            variant="outline"
            className="mt-3"
            onClick={handleExport}
            disabled={yamlText.trim().length === 0}
          >
            <Upload />
            JSON으로 내보내기
          </Button>
        </div>
        <div className="rounded border bg-[#f5f9fe] p-4">
          <Download className="mx-auto my-6 size-7 text-primary" />
          <div className="text-[13px] font-semibold">Import ontology</div>
          <p className="mt-1 text-xs text-muted-foreground">
            가져온 온톨로지는 현재 온톨로지를 대체하거나 병합할 수 있습니다.
          </p>
          <div className="mt-3 space-y-2">
            <Textarea
              value={importText}
              onChange={(event) => setImportText(event.target.value)}
              placeholder="온톨로지 JSON을 붙여넣으세요…"
              spellCheck={false}
              className="min-h-24 font-mono text-[11px]"
            />
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={() => applyImport(importText)}
                disabled={importText.trim().length === 0}
              >
                편집기에 반영
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
              >
                파일 선택
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".json,.yaml,.yml,application/json"
                onChange={handleFileChange}
                className="hidden"
              />
            </div>
            {importError ? (
              <p className="text-[11px] text-destructive">{importError}</p>
            ) : null}
          </div>
        </div>
      </div>

      <DraftPanel
        yamlText={yamlText}
        isDraftDirty={isDraftDirty}
        branchDetail={branchDetail}
        updateMutation={updateMutation}
        activeVersionNumber={activeVersionNumber}
        onYamlChange={onYamlChange}
        onSaveToBranch={onSaveToBranch}
        onOntologyChanged={onOntologyChanged}
      />
    </div>
  );
}
