import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

import { WORKSHOP_TEMPLATES } from "../lib/templates";

export function TemplateGallery({
  open,
  onOpenChange,
  onPick,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (templateId: string) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>템플릿으로 시작</DialogTitle>
          <DialogDescription>
            미리 구성된 앱을 선택하면 현재 온톨로지에 자동 바인딩되어 즉시
            생성됩니다.
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          {WORKSHOP_TEMPLATES.map((template) => (
            <button
              key={template.id}
              type="button"
              onClick={() => {
                onPick(template.id);
                onOpenChange(false);
              }}
              className={cn(
                "flex flex-col gap-2 rounded-lg border border-[#d5dce1] p-3 text-left hover:border-[#2d72d2] hover:bg-[#f6f8fa]",
              )}
            >
              <div className="flex items-center gap-2">
                <span className="flex size-8 items-center justify-center rounded-md bg-[#2d72d2]/10 text-[#2d72d2]">
                  <template.icon className="size-4" />
                </span>
                <span className="text-[13px] font-semibold text-[#1c2127]">
                  {template.name}
                </span>
              </div>
              <span className="text-[11px] leading-relaxed text-[#5f6b7c]">
                {template.description}
              </span>
              <div className="flex flex-wrap gap-1">
                {template.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded bg-[#eef1f4] px-1.5 py-0.5 text-[10px] text-[#5f6b7c]"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
