import type { CSSProperties } from "react";

import type { SectionLayout, SectionPadding, SectionStyle } from "./app-model";

const PADDING_PX: Record<SectionPadding, string> = {
  none: "0px",
  compact: "12px",
  regular: "20px",
  large: "32px",
};

export function sectionPaddingPx(padding: SectionPadding): string {
  return PADDING_PX[padding] ?? "20px";
}

/** 섹션 컨테이너 인라인 스타일: 배경·패딩·테두리/그림자. */
export function sectionStyleProps(style: SectionStyle): CSSProperties {
  const css: CSSProperties = {
    padding: sectionPaddingPx(style.padding),
  };
  if (style.background && style.background !== "transparent") {
    css.background = style.background;
  }
  if (style.border === "bordered") {
    css.border = "1px solid #d5dce1";
    css.borderRadius = "8px";
  } else if (style.border === "shadow") {
    css.borderRadius = "8px";
    css.boxShadow = "0 1px 3px rgba(16,22,26,0.1), 0 0 0 1px #e4e9ed";
    css.background = css.background ?? "#ffffff";
  }
  return css;
}

/** 위젯 배치 컨테이너 클래스 (탭 레이아웃은 렌더러가 별도 처리). */
export function layoutContainerClass(layout: SectionLayout): string {
  switch (layout) {
    case "columns":
      return "grid gap-3 items-start [grid-template-columns:repeat(auto-fit,minmax(240px,1fr))]";
    case "rows":
      return "flex gap-3 overflow-x-auto pb-1";
    case "toolbar":
      return "flex flex-wrap items-center gap-3";
    case "flow":
    default:
      return "flex flex-col gap-3";
  }
}

/** rows 레이아웃에서 각 위젯이 가지는 최소 너비 클래스. */
export function widgetSlotClass(layout: SectionLayout): string {
  if (layout === "rows") return "min-w-[280px] shrink-0";
  if (layout === "toolbar") return "min-w-[160px]";
  return "min-w-0";
}
