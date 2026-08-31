import type { GenericObject } from "@foundry-lite/sdk";
import {
  Check,
  ChevronDown,
  Pencil,
  Play,
  X,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

import { businessActionName, businessObjectTypeName } from "../../lib/business-display";
import { useRuntimeDispatch, useRuntimeState } from "../../lib/runtime-state";
import { actionsForObject } from "../../lib/ontology-context";
import { RuntimeActionForm } from "../RuntimeActionForm";
import { useWorkshopRuntimeDefinition } from "../runtime-application-context";
import {
  actionViewFor,
  objectViewFor,
  useWidgetObjects,
  WidgetFrame,
  WidgetPlaceholder,
  type WidgetRuntimeProps,
} from "./widget-kit";

const MISSING_OBJECT = (
  <WidgetPlaceholder
    label="업무 데이터 연결이 필요합니다"
    hint="AI FDE에게 이 화면에서 어떤 업무를 처리할지 알려주세요."
  />
);

function selectedObjectFrom(
  objects: readonly GenericObject[],
  selectedObjectId: string | null,
): GenericObject | null {
  if (!selectedObjectId) return null;
  return objects.find((object) => object.objectId === selectedObjectId) ?? null;
}

/** 액션 폼: 선택 객체에 단일 액션을 폼으로 실행 (검증·멱등·낙관적 잠금). */
export function ActionFormWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { allObjects } = useWidgetObjects(objectApiName);
  const state = useRuntimeState();
  const dispatch = useRuntimeDispatch();
  const definition = useWorkshopRuntimeDefinition();
  const actionView = actionViewFor(props, widget.config.actionApiName);

  if (!objectApiName) return MISSING_OBJECT;

  const object = selectedObjectFrom(allObjects, state.selectedObjectId);

  return (
    <WidgetFrame
      title={widget.config.title || (actionView ? businessActionName(actionView.apiName, actionView, definition.presentation) : "업무 처리")}
      className="min-h-[200px]"
      bodyClassName="p-3"
    >
      {!actionView ? (
        <WidgetPlaceholder
          label="실행할 업무가 정해지지 않았습니다"
          hint="누가 어떤 일을 할 수 있어야 하는지 AI FDE에게 알려주세요."
        />
      ) : !object ? (
        <WidgetPlaceholder
          label="처리할 업무를 선택해 주세요"
          hint="목록에서 업무를 선택하면 가능한 다음 행동을 보여드립니다."
        />
      ) : (
        <RuntimeActionForm
          key={`${actionView.apiName}:${object.objectId}`}
          actionView={actionView}
          targetObject={object}
          onApplied={() => dispatch({ type: "bumpData" })}
          onCancel={() => dispatch({ type: "selectObject", objectId: null })}
          requiresHumanConfirmation={widget.config.humanApprovalActionApiNames?.includes(actionView.apiName) === true}
        />
      )}
    </WidgetFrame>
  );
}

/** 액션 이름으로 버튼 intent 색·아이콘을 추론 (Palantir 컬러 관례). */
function actionButtonStyle(apiName: string): { bg: string; icon: LucideIcon } {
  const name = apiName.toLowerCase();
  if (/approve|resolve|confirm|complete|activate|accept|assign/.test(name))
    return { bg: "bg-[#238551] hover:bg-[#1c7048]", icon: Check };
  if (/reject|delete|cancel|remove|deactivate|purge|discard/.test(name))
    return { bg: "bg-[#cd4246] hover:bg-[#b83a3e]", icon: X };
  if (/adjust|edit|modify|update|change|revise/.test(name))
    return { bg: "bg-[#c87619] hover:bg-[#ad6614]", icon: Pencil };
  return { bg: "bg-[#2d72d2] hover:bg-[#215db0]", icon: Play };
}

/** 버튼 그룹: 선택 객체에 실행 가능한 액션 버튼들. 클릭 시 인라인 폼 전개. */
export function ButtonGroupWidget(props: WidgetRuntimeProps) {
  const { widget } = props;
  const objectApiName = widget.config.objectApiName ?? null;
  const { allObjects } = useWidgetObjects(objectApiName);
  const objectView = objectViewFor(props, objectApiName);
  const state = useRuntimeState();
  const dispatch = useRuntimeDispatch();
  const definition = useWorkshopRuntimeDefinition();
  const [activeAction, setActiveAction] = useState<string | null>(null);

  if (!objectApiName) return MISSING_OBJECT;

  const configured =
    widget.config.actionApiNames && widget.config.actionApiNames.length > 0
      ? widget.config.actionApiNames
      : actionsForObject(props.actionViews, objectApiName);
  const actionViews = configured
    .map((apiName) => actionViewFor(props, apiName))
    .filter((view): view is NonNullable<typeof view> => view !== null);

  const object = selectedObjectFrom(allObjects, state.selectedObjectId);
  const activeView = actionViews.find((view) => view.apiName === activeAction);

  return (
    <WidgetFrame
      title={widget.config.title || "다음 업무"}
      subtitle={objectView ? businessObjectTypeName(objectView.apiName, objectView, definition.presentation) : undefined}
      className="min-h-[120px]"
      bodyClassName="p-3 space-y-3"
    >
      {actionViews.length === 0 ? (
        <WidgetPlaceholder label="지금 실행할 수 있는 업무가 없습니다" />
      ) : (
        <div className="flex flex-wrap gap-2">
          {actionViews.map((view) => {
            const isActive = view.apiName === activeAction;
            const style = actionButtonStyle(view.apiName);
            const Icon = style.icon;
            return (
              <button
                key={view.apiName}
                type="button"
                disabled={!object}
                onClick={() =>
                  setActiveAction((current) =>
                    current === view.apiName ? null : view.apiName,
                  )
                }
                className={cn(
                  "flex min-h-10 items-center gap-2 rounded-xl px-4 py-2 text-[13px] font-bold text-white shadow-sm",
                  style.bg,
                  isActive && "ring-2 ring-[#1c2127]/25 ring-offset-1",
                  !object && "cursor-not-allowed opacity-40",
                )}
              >
                <Icon className="size-3.5" strokeWidth={2.5} />
                {businessActionName(view.apiName, view, definition.presentation)}
                <ChevronDown
                  className={cn(
                    "size-3 transition-transform",
                    isActive && "rotate-180",
                  )}
                />
              </button>
            );
          })}
        </div>
      )}
      {!object ? (
        <p className="text-[12px] leading-5 text-muted-foreground">
          목록에서 처리할 업무를 선택하면 가능한 다음 행동이 활성화됩니다.
        </p>
      ) : activeView ? (
        <div className="rounded-xl bg-[var(--workshop-subtle)] p-3.5">
          <RuntimeActionForm
            key={`${activeView.apiName}:${object.objectId}`}
            actionView={activeView}
            targetObject={object}
            onApplied={() => {
              dispatch({ type: "bumpData" });
              setActiveAction(null);
            }}
            onCancel={() => setActiveAction(null)}
            requiresHumanConfirmation={widget.config.humanApprovalActionApiNames?.includes(activeView.apiName) === true}
          />
        </div>
      ) : null}
    </WidgetFrame>
  );
}
