import type { OntologyDraftProperty } from "@foundry-lite/sdk/ontology-draft";
import { ontologyPropertyApiNameForColumn } from "@foundry-lite/sdk/ontology-draft";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { ALLOWED_PROPERTY_TYPES } from "../lib/object-type-draft";

interface NewPropertyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 백킹 컬럼 후보 (없으면 derived 속성). */
  columnNames: string[];
  existingApiNames: string[];
  onCreate: (property: OntologyDraftProperty) => void;
}

/** 새 속성 추가 다이얼로그 — 컬럼 미지정 시 derived 속성을 만든다. */
export function NewPropertyDialog({
  open,
  onOpenChange,
  columnNames,
  existingApiNames,
  onCreate,
}: NewPropertyDialogProps) {
  const [displayName, setDisplayName] = useState("");
  const [apiName, setApiName] = useState("");
  const [isApiNameManual, setIsApiNameManual] = useState(false);
  const [type, setType] = useState<string>("string");
  const [column, setColumn] = useState<string>("__none__");

  const reset = () => {
    setDisplayName("");
    setApiName("");
    setIsApiNameManual(false);
    setType("string");
    setColumn("__none__");
  };

  const handleDisplayNameChange = (value: string) => {
    setDisplayName(value);
    if (!isApiNameManual) {
      setApiName(ontologyPropertyApiNameForColumn(value));
    }
  };

  const isDuplicate = apiName.length > 0 && existingApiNames.includes(apiName);
  const canCreate = apiName.trim().length > 0 && !isDuplicate;

  const handleCreate = () => {
    const property: OntologyDraftProperty = {
      apiName: apiName.trim(),
      type,
      displayName: displayName.trim() || null,
    };
    if (column !== "__none__") property.column = column;
    onCreate(property);
    reset();
    onOpenChange(false);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value) reset();
        onOpenChange(value);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>새 속성</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-xs">Display name</Label>
            <Input
              value={displayName}
              onChange={(event) => handleDisplayNameChange(event.target.value)}
              placeholder="예: 승인 메모"
              className="h-8 text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">API name</Label>
            <Input
              value={apiName}
              onChange={(event) => {
                setIsApiNameManual(true);
                setApiName(event.target.value);
              }}
              placeholder="예: approvalNote"
              className="h-8 font-mono text-xs"
            />
            {isDuplicate ? (
              <p className="text-[11px] text-destructive">
                이미 존재하는 API 이름입니다.
              </p>
            ) : null}
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Property type</Label>
            <Select value={type} onValueChange={setType}>
              <SelectTrigger size="sm" className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ALLOWED_PROPERTY_TYPES.map((item) => (
                  <SelectItem key={item} value={item} className="text-xs">
                    {item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Backing column</Label>
            <Select value={column} onValueChange={setColumn}>
              <SelectTrigger size="sm" className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__" className="text-xs">
                  derived / edit-layer
                </SelectItem>
                {columnNames.map((item) => (
                  <SelectItem
                    key={item}
                    value={item}
                    className="font-mono text-xs"
                  >
                    {item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button
            size="sm"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            취소
          </Button>
          <Button size="sm" disabled={!canCreate} onClick={handleCreate}>
            속성 추가
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
