import { ArrowRight, Pencil, Plus, Search } from "lucide-react";

import type { WidgetKind } from "../lib/app-model";

/** 위젯 종류별 미리보기 썸네일 (Palantir Add widget 피커의 스켈레톤 프리뷰). */
export function WidgetThumb({ kind }: { kind: WidgetKind }) {
  switch (kind) {
    case "objectTable":
      return <TableThumb />;
    case "objectList":
      return <ListThumb />;
    case "objectDetail":
      return <DetailThumb />;
    case "objectSetTitle":
      return <SetTitleThumb />;
    case "links":
      return <LinksThumb />;
    case "metricCard":
      return <MetricThumb />;
    case "barChart":
      return <BarThumb />;
    case "pieChart":
      return <PieThumb />;
    case "timeline":
      return <TimelineThumb />;
    case "filterList":
      return <FilterThumb />;
    case "objectDropdown":
      return <DropdownThumb />;
    case "searchBar":
      return <SearchThumb />;
    case "stringSelector":
      return <ChipsThumb />;
    case "buttonGroup":
      return <ButtonGroupThumb />;
    case "actionForm":
      return <ActionFormThumb />;
    case "markdown":
      return <MarkdownThumb />;
    case "sectionHeader":
      return <HeaderThumb />;
    case "divider":
      return <DividerThumb />;
    case "aipChatbot":
      return <ChatThumb />;
    default:
      return <div className="h-full w-full" />;
  }
}

const BAR = "block h-1.5 rounded bg-[#dbe1e8]";
const CARD = "w-full rounded-md border border-[#e4e9ed] bg-white p-2 shadow-sm";

function Bar({ w }: { w: string }) {
  return <span className={BAR} style={{ width: w }} />;
}

function TableThumb() {
  return (
    <div className={CARD}>
      <div className="mb-1 flex gap-2 border-b border-[#eef1f4] pb-1">
        {["30%", "24%", "20%"].map((w, i) => (
          <span
            key={i}
            className="block h-1.5 rounded bg-[#c5ccd3]"
            style={{ width: w }}
          />
        ))}
      </div>
      {[0, 1, 2, 3].map((row) => (
        <div key={row} className="flex items-center gap-2 py-0.5">
          <span className="size-2.5 shrink-0 rounded-[2px] bg-[#2d72d2]" />
          <Bar w="30%" />
          <Bar w="24%" />
          <Bar w="20%" />
        </div>
      ))}
    </div>
  );
}

function ListThumb() {
  return (
    <div className="w-full space-y-1.5">
      {[0, 1, 2].map((row) => (
        <div
          key={row}
          className="flex items-center gap-2 rounded border border-[#e4e9ed] bg-white p-1.5"
        >
          <span className="size-4 shrink-0 rounded bg-[#2d72d2]/15" />
          <span className="flex-1 space-y-1">
            <Bar w="60%" />
            <Bar w="40%" />
          </span>
        </div>
      ))}
    </div>
  );
}

function DetailThumb() {
  return (
    <div className={CARD}>
      {[0, 1, 2, 3].map((row) => (
        <div key={row} className="flex items-center justify-between gap-2 py-1">
          <Bar w="30%" />
          <span
            className="block h-1.5 rounded bg-[#c5ccd3]"
            style={{ width: "40%" }}
          />
        </div>
      ))}
    </div>
  );
}

function SetTitleThumb() {
  return (
    <div className="flex w-full items-center gap-2">
      <span className="flex size-8 items-center justify-center rounded-md bg-[#2d72d2]/15 text-[13px] font-bold text-[#2d72d2]">
        O
      </span>
      <span className="space-y-1">
        <span className="block h-2 w-20 rounded bg-[#c5ccd3]" />
        <span className="block h-1.5 w-12 rounded bg-[#dbe1e8]" />
      </span>
    </div>
  );
}

function LinksThumb() {
  return (
    <div className="w-full space-y-1.5">
      {[0, 1].map((row) => (
        <div
          key={row}
          className="flex items-center gap-2 rounded border border-[#e4e9ed] bg-white p-1.5"
        >
          <Bar w="30%" />
          <ArrowRight className="size-3 text-[#00847a]" />
          <Bar w="30%" />
        </div>
      ))}
    </div>
  );
}

function MetricThumb() {
  return (
    <div className={CARD}>
      <div className="flex items-baseline gap-2">
        <span className="text-[24px] leading-none font-bold text-[#1c2127]">
          6,351
        </span>
        <span className="text-[11px] font-semibold text-[#238551]">+40%</span>
      </div>
      <span className="mt-1 block h-1.5 w-14 rounded bg-[#dbe1e8]" />
    </div>
  );
}

function BarThumb() {
  const bars = [
    { h: 70, c: "#2d72d2" },
    { h: 45, c: "#e8963f" },
    { h: 85, c: "#2d72d2" },
    { h: 30, c: "#e8963f" },
    { h: 55, c: "#2d72d2" },
  ];
  return (
    <div className="flex h-16 w-full items-end justify-center gap-1.5">
      {bars.map((bar, i) => (
        <span
          key={i}
          className="w-3 rounded-t-[2px]"
          style={{ height: `${bar.h}%`, backgroundColor: bar.c }}
        />
      ))}
    </div>
  );
}

function PieThumb() {
  return (
    <div
      className="size-14 rounded-full"
      style={{
        background:
          "conic-gradient(#2d72d2 0 55%, #238551 55% 80%, #e8963f 80% 100%)",
      }}
    >
      <div className="m-[18%] size-[64%] rounded-full bg-[#f4f6f8]" />
    </div>
  );
}

function TimelineThumb() {
  return (
    <div className="w-full space-y-2 border-l-2 border-[#d5dce1] pl-3">
      {[0, 1, 2].map((row) => (
        <div key={row} className="relative">
          <span className="absolute top-0.5 -left-[15px] size-2 rounded-full bg-[#2d72d2]" />
          <Bar w="70%" />
        </div>
      ))}
    </div>
  );
}

function FilterThumb() {
  const rows = [
    { w: "70%", n: "80", bar: "80%" },
    { w: "60%", n: "60", bar: "60%", checked: true },
    { w: "55%", n: "30", bar: "30%", checked: true },
    { w: "50%", n: "10", bar: "15%" },
  ];
  return (
    <div className="w-full space-y-1">
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <span
            className={`size-2.5 shrink-0 rounded-[2px] border ${
              row.checked ? "border-[#2d72d2] bg-[#2d72d2]" : "border-[#a7b1bd]"
            }`}
          />
          <Bar w={row.w} />
          <span className="ml-auto text-[8px] text-[#8f99a8]">{row.n}</span>
          <span
            className="h-1 rounded bg-[#2d72d2]/50"
            style={{ width: row.bar === "80%" ? 14 : 8 }}
          />
        </div>
      ))}
    </div>
  );
}

function DropdownThumb() {
  return (
    <div className="w-full space-y-1.5">
      <span className="block h-1.5 w-10 rounded bg-[#dbe1e8]" />
      <div className="flex items-center justify-between rounded border border-[#c5ccd3] bg-white px-2 py-1.5">
        <Bar w="50%" />
        <span className="size-0 border-x-4 border-t-4 border-x-transparent border-t-[#8f99a8]" />
      </div>
    </div>
  );
}

function SearchThumb() {
  return (
    <div className="flex w-full items-center gap-2 rounded border border-[#c5ccd3] bg-white px-2 py-1.5">
      <Search className="size-3 text-[#8f99a8]" />
      <Bar w="60%" />
    </div>
  );
}

function ChipsThumb() {
  return (
    <div className="flex w-full flex-wrap gap-1.5">
      {["24px", "34px", "28px", "40px", "22px"].map((w, i) => (
        <span
          key={i}
          className="rounded-full border border-[#c5ccd3] bg-white px-1.5 py-1"
          style={{ width: w }}
        >
          <span className="block h-1 rounded bg-[#dbe1e8]" />
        </span>
      ))}
    </div>
  );
}

function ButtonGroupThumb() {
  return (
    <div className="flex w-full items-center gap-2">
      <span className="flex flex-1 items-center gap-1.5 rounded-md bg-[#2d72d2] px-2 py-2">
        <Plus className="size-3 text-white" />
        <span className="block h-1.5 flex-1 rounded bg-white/70" />
      </span>
      <span className="flex items-center gap-1.5 rounded-md border border-[#c5ccd3] bg-white px-2 py-2">
        <Pencil className="size-3 text-[#5f6b7c]" />
        <span className="block h-1.5 w-6 rounded bg-[#dbe1e8]" />
      </span>
    </div>
  );
}

function ActionFormThumb() {
  return (
    <div className={CARD}>
      <span className="block h-1.5 w-10 rounded bg-[#dbe1e8]" />
      <div className="my-1.5 rounded border border-[#c5ccd3] bg-white px-2 py-1.5">
        <Bar w="60%" />
      </div>
      <span className="ml-auto block h-4 w-12 rounded bg-[#238551]" />
    </div>
  );
}

function MarkdownThumb() {
  return (
    <div className="w-full space-y-1.5">
      <span className="block h-2.5 w-24 rounded bg-[#c5ccd3]" />
      <Bar w="90%" />
      <Bar w="80%" />
      <Bar w="60%" />
    </div>
  );
}

function HeaderThumb() {
  return <span className="block h-3 w-28 rounded bg-[#c5ccd3]" />;
}

function DividerThumb() {
  return <span className="block h-px w-full bg-[#c5ccd3]" />;
}

function ChatThumb() {
  return (
    <div className="w-full space-y-1.5">
      <span className="block w-3/4 rounded-lg bg-[#f1ecfb] p-1.5">
        <span className="block h-1.5 rounded bg-[#c9bbef]" />
      </span>
      <span className="ml-auto block w-2/3 rounded-lg bg-[#e8f0fb] p-1.5">
        <span className="block h-1.5 rounded bg-[#a9c9ef]" />
      </span>
    </div>
  );
}
