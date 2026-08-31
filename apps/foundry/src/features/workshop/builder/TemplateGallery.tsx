import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ArrowRight,
  BriefcaseBusiness,
  Calculator,
  HeartPulse,
  UsersRound,
} from "lucide-react";
import { Link } from "react-router";

import { SOLUTION_PLAYBOOKS } from "@/features/aip/solution-playbooks";
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
      <DialogContent className="max-h-[88vh] max-w-4xl overflow-auto">
        <DialogHeader>
          <DialogTitle>템플릿으로 시작</DialogTitle>
          <DialogDescription>
            업종별 업무 청사진에서 전체 서비스를 설계하거나, 현재 앱에 필요한
            화면 하나만 빠르게 추가할 수 있습니다.
          </DialogDescription>
        </DialogHeader>
        <section className="rounded-2xl bg-[#111827] p-4 text-white">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="text-[10px] font-bold tracking-[.12em] text-sky-300">
                상용 SaaS 청사진
              </div>
              <h2 className="mt-1 text-[17px] font-bold tracking-[-.025em]">
                화면이 아니라 업무 제품 전체를 먼저 설계하세요
              </h2>
              <p className="mt-1 max-w-2xl text-[11px] leading-5 text-white/60">
                사용자, 기록, 업무 흐름, 승인, 증거와 운영 준비를 함께 정리한 뒤
                같은 정의로 GPT 화면과 외부 앱을 만듭니다.
              </p>
            </div>
            <span className="rounded-full bg-emerald-400/15 px-3 py-1.5 text-[10px] font-semibold text-emerald-200">
              개발 용어 없이 시작
            </span>
          </div>
          <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {SOLUTION_PLAYBOOKS.map((playbook) => {
              const Icon = SOLUTION_ICONS[playbook.id];
              return (
                <Link
                  key={playbook.id}
                  to={`/aip?solution=${playbook.id}`}
                  className="group rounded-xl border border-white/10 bg-white/6 p-3 transition hover:border-sky-300/50 hover:bg-white/10"
                >
                  <div className="flex items-center gap-2">
                    <span className="grid size-8 place-items-center rounded-lg bg-white/10">
                      <Icon className="size-4 text-sky-200" />
                    </span>
                    <div>
                      <div className="text-[9px] font-bold text-sky-300">
                        {playbook.eyebrow}
                      </div>
                      <div className="text-[11px] font-semibold">{playbook.name}</div>
                    </div>
                  </div>
                  <div className="mt-3 flex items-center justify-between text-[9px] text-white/55">
                    <span>{playbook.signature}</span>
                    <ArrowRight className="size-3 transition group-hover:translate-x-0.5" />
                  </div>
                </Link>
              );
            })}
          </div>
        </section>
        <div className="flex items-center gap-3 pt-1">
          <div className="h-px flex-1 bg-[#e1e6eb]" />
          <span className="text-[10px] font-bold text-[#7b8798]">현재 앱에 화면 추가</span>
          <div className="h-px flex-1 bg-[#e1e6eb]" />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
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

const SOLUTION_ICONS = {
  hospital: HeartPulse,
  accounting: Calculator,
  crm: BriefcaseBusiness,
  hr: UsersRound,
} as const;
