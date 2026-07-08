import { useState } from "react";
import { Bot, Send, Sparkles } from "lucide-react";

import { cn } from "@/lib/utils";

import { WidgetFrame, type WidgetRuntimeProps } from "./widget-kit";

/** `**bold**` 인라인 마크업을 <strong>으로 렌더한다. */
function renderInline(text: string) {
  const segments = text.split("**");
  return segments.map((segment, index) =>
    index % 2 === 1 ? (
      <strong key={index} className="font-semibold text-[#1c2127]">
        {segment}
      </strong>
    ) : (
      <span key={index}>{segment}</span>
    ),
  );
}

type MarkdownBlock =
  | { kind: "h1" | "h2" | "h3" | "p"; text: string }
  | { kind: "ul"; items: string[] };

/** 마크다운 문자열을 블록 배열로 파싱한다 (의존성 없는 경량 파서). */
function parseMarkdown(source: string): MarkdownBlock[] {
  const lines = source.split("\n");
  const blocks: MarkdownBlock[] = [];
  let listItems: string[] = [];

  const flushList = () => {
    if (listItems.length > 0) {
      blocks.push({ kind: "ul", items: listItems });
      listItems = [];
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (line.startsWith("- ")) {
      listItems.push(line.slice(2));
      continue;
    }
    flushList();
    if (line.trim() === "") continue;
    if (line.startsWith("### ")) {
      blocks.push({ kind: "h3", text: line.slice(4) });
    } else if (line.startsWith("## ")) {
      blocks.push({ kind: "h2", text: line.slice(3) });
    } else if (line.startsWith("# ")) {
      blocks.push({ kind: "h1", text: line.slice(2) });
    } else {
      blocks.push({ kind: "p", text: line });
    }
  }
  flushList();
  return blocks;
}

/** 마크다운 텍스트를 sanitized JSX로 렌더하는 위젯. */
export function MarkdownWidget(props: WidgetRuntimeProps) {
  const text = props.widget.config.text;

  if (!text || text.trim() === "") {
    return (
      <WidgetFrame borderless>
        <div className="p-3 text-[12px] text-[#8f99a8]">
          마크다운 텍스트를 입력하세요.
        </div>
      </WidgetFrame>
    );
  }

  const blocks = parseMarkdown(text);

  return (
    <WidgetFrame borderless>
      <div className="space-y-2 p-3">
        {blocks.map((block, index) => {
          if (block.kind === "h1") {
            return (
              <h1 key={index} className="text-[16px] font-bold text-[#1c2127]">
                {renderInline(block.text)}
              </h1>
            );
          }
          if (block.kind === "h2") {
            return (
              <h2
                key={index}
                className="text-[14px] font-semibold text-[#1c2127]"
              >
                {renderInline(block.text)}
              </h2>
            );
          }
          if (block.kind === "h3") {
            return (
              <h3
                key={index}
                className="text-[13px] font-semibold text-[#1c2127]"
              >
                {renderInline(block.text)}
              </h3>
            );
          }
          if (block.kind === "ul") {
            return (
              <ul
                key={index}
                className="list-disc pl-4 text-[12px] text-[#404854]"
              >
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{renderInline(item)}</li>
                ))}
              </ul>
            );
          }
          return (
            <p
              key={index}
              className="text-[12px] leading-relaxed text-[#404854]"
            >
              {renderInline(block.text)}
            </p>
          );
        })}
      </div>
    </WidgetFrame>
  );
}

/** 섹션 제목 헤딩 위젯 (프레임/보더 없음). */
export function SectionHeaderWidget(props: WidgetRuntimeProps) {
  const text = props.widget.config.text || "섹션 제목";
  return (
    <div className="px-1 py-1">
      <h3 className="text-[15px] font-bold text-[#1c2127]">{text}</h3>
    </div>
  );
}

/** 수평 구분선 위젯 (프레임 없음). */
export function DividerWidget(_props: WidgetRuntimeProps) {
  return (
    <div className="py-2">
      <div className="h-px w-full bg-[#d5dce1]" />
    </div>
  );
}

type ChatMessage = { id: number; role: "assistant" | "user"; text: string };

const SEED_MESSAGES: ChatMessage[] = [
  {
    id: 0,
    role: "assistant",
    text: "온톨로지 컨텍스트에 연결된 어시스턴트입니다. 객체·액션에 대해 질문해 보세요.",
  },
  {
    id: 1,
    role: "user",
    text: "현재 활성 상태인 객체가 몇 개인가요?",
  },
];

const CANNED_REPLY =
  "이 위젯은 미리보기입니다. 전체 기능은 AIP 화면에서 제공됩니다.";

/** Workshop 임베드용 경량 AIP 챗봇 미리보기 (백엔드 호출 없음). */
export function AipChatbotWidget(props: WidgetRuntimeProps) {
  const [messages, setMessages] = useState<ChatMessage[]>(SEED_MESSAGES);
  const [draft, setDraft] = useState("");

  const handleSend = () => {
    const trimmed = draft.trim();
    if (trimmed === "") return;
    setMessages((prev) => {
      const baseId = prev.length > 0 ? prev[prev.length - 1].id + 1 : 0;
      return [
        ...prev,
        { id: baseId, role: "user", text: trimmed },
        { id: baseId + 1, role: "assistant", text: CANNED_REPLY },
      ];
    });
    setDraft("");
  };

  return (
    <WidgetFrame
      title={props.widget.config.title || "AIP Assistant"}
      subtitle="preview"
      actions={<Bot className="size-4 shrink-0 text-[#7961db]" />}
      bodyClassName="flex flex-col"
    >
      <div className="flex min-h-[200px] flex-1 flex-col gap-2 overflow-y-auto p-3">
        {messages.map((message) =>
          message.role === "assistant" ? (
            <div
              key={message.id}
              className="flex max-w-[85%] items-start gap-2 self-start"
            >
              <div className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-[#7961db]">
                <Sparkles className="size-3 text-white" />
              </div>
              <div className="rounded-lg bg-[#f1ecfb] p-2 text-[12px] text-[#404854]">
                {message.text}
              </div>
            </div>
          ) : (
            <div
              key={message.id}
              className="max-w-[85%] self-end rounded-lg bg-[#e8f0fb] p-2 text-[12px] text-[#404854]"
            >
              {message.text}
            </div>
          ),
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2 border-t border-[#e4e9ed] p-2">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              handleSend();
            }
          }}
          placeholder="어시스턴트에게 질문하기..."
          className={cn(
            "min-w-0 flex-1 rounded-md border border-[#d5dce1] bg-[#f6f8fa] px-2 py-1.5",
            "text-[12px] text-[#1c2127] placeholder:text-[#8f99a8]",
            "focus:border-[#7961db] focus:outline-none",
          )}
        />
        <button
          type="button"
          onClick={handleSend}
          aria-label="전송"
          className={cn(
            "flex size-7 shrink-0 items-center justify-center rounded-md",
            "bg-[#7961db] text-white transition-opacity hover:opacity-90",
          )}
        >
          <Send className="size-3.5" />
        </button>
      </div>
    </WidgetFrame>
  );
}
