import { useEffect, useRef, useState } from "react";
import { Bot, Wrench, ChevronDown, ChevronRight, Send } from "lucide-react";
import { streamSSE } from "../api";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  tools?: { name: string; args: Record<string, unknown>; result: unknown }[];
  error?: boolean;
}

const SUGGESTIONS = [
  "现在哪个站有空闲快充桩？",
  "我的余额还能充多少度电？",
  "最近充电价格什么时候最便宜？",
  "帮我看下最近的充电记录花了多少钱",
];

const TOOL_LABEL: Record<string, string> = {
  get_stations: "查询站点列表",
  get_pile_status: "查询桩位状态",
  get_balance: "查询账户余额",
  get_price_policy: "查询电价策略",
  get_my_sessions: "查询我的充电记录",
  get_active_session: "查询进行中会话",
};

function ToolCallChip({ tool }: { tool: { name: string; args: Record<string, unknown>; result: unknown } }) {
  const [open, setOpen] = useState(false);
  const label = TOOL_LABEL[tool.name] ?? tool.name;
  return (
    <div style={{ margin: "4px 0" }}>
      <button
        className="btn small"
        style={{ display: "inline-flex", gap: 6, alignItems: "center" }}
        onClick={() => setOpen(!open)}
      >
        <Wrench size={12} /> {label}
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {open && (
        <div
          style={{
            background: "#f8fafd",
            border: "1px solid var(--border)",
            borderRadius: 9,
            padding: "8px 12px",
            marginTop: 6,
            fontSize: 12,
            fontFamily: "JetBrains Mono, Consolas, monospace",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
            color: "var(--text-dim)",
          }}
        >
          args: {JSON.stringify(tool.args)}
          {"\n"}result: {JSON.stringify(tool.result, null, 0).slice(0, 600)}
        </div>
      )}
    </div>
  );
}

export default function Assistant() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages]);

  const send = async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || streaming) return;
    setInput("");
    const history = [...messages, { role: "user" as const, content }];
    setMessages(history);
    setStreaming(true);
    const assistantMsg: ChatMessage = { role: "assistant", content: "", tools: [] };
    setMessages([...history, assistantMsg]);

    const update = (patch: Partial<ChatMessage>) => {
      setMessages((cur) => {
        const next = [...cur];
        next[next.length - 1] = { ...next[next.length - 1], ...patch };
        return next;
      });
    };

    try {
      await streamSSE(
        "/ai/chat",
        { messages: history.map((m) => ({ role: m.role, content: m.content })) },
        (event, data) => {
          if (event === "delta") {
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant") {
                next[next.length - 1] = { ...last, content: last.content + String(data.content ?? "") };
              }
              return next;
            });
          } else if (event === "tool") {
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant") {
                next[next.length - 1] = {
                  ...last,
                  tools: [...(last.tools ?? []), { name: String(data.name), args: (data.args as Record<string, unknown>) ?? {}, result: data.result }],
                };
              }
              return next;
            });
          } else if (event === "error") {
            update({ error: true, content: String(data.message ?? "AI 服务暂不可用") });
          }
        }
      );
    } catch {
      update({ error: true, content: "AI 服务连接失败，请稍后再试" });
    } finally {
      setStreaming(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 44px)" }}>
      <div className="page-head">
        <span className="page-title">AI 助手</span>
        <span className="page-sub">
          DeepSeek Function Calling：AI 决策调用哪些平台工具，工具结果回填后生成回答
        </span>
      </div>

      <div
        ref={listRef}
        className="card"
        style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 14 }}
      >
        {messages.length === 0 && (
          <div className="empty" style={{ margin: "auto" }}>
            <div
              style={{
                width: 64, height: 64, borderRadius: 18, margin: "0 auto 14px",
                background: "var(--accent-soft)", display: "flex", alignItems: "center", justifyContent: "center",
              }}
            >
              <Bot size={34} color="var(--accent)" strokeWidth={1.8} />
            </div>
            试试这些提问：
            <div style={{ display: "grid", gap: 8, marginTop: 14, maxWidth: 420, marginInline: "auto" }}>
              {SUGGESTIONS.map((s) => (
                <button key={s} className="btn" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "76%",
              background: m.role === "user" ? "linear-gradient(135deg,#2563eb,#0891b2)" : "#f8fafd",
              color: m.role === "user" ? "#ffffff" : m.error ? "var(--red)" : "var(--text)",
              border: m.role === "user" ? "none" : "1px solid var(--border)",
              boxShadow: m.role === "user" ? "0 3px 10px rgba(37,99,235,0.25)" : "var(--shadow-sm)",
              borderRadius: m.role === "user" ? "14px 14px 4px 14px" : "14px 14px 14px 4px",
              padding: "10px 14px",
              fontSize: 13.5,
              whiteSpace: "pre-wrap",
              lineHeight: 1.65,
            }}
          >
            {m.tools && m.tools.length > 0 && (
              <div style={{ marginBottom: 8 }}>
                {m.tools.map((t, j) => (
                  <ToolCallChip key={j} tool={t} />
                ))}
              </div>
            )}
            {m.content || (streaming && i === messages.length - 1 ? "▍" : "")}
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
        <input
          className="input"
          style={{ fontSize: 14 }}
          placeholder="向 AI 助手提问…（可查询站点、桩位、余额、电价、充电记录）"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
          disabled={streaming}
        />
        <button
          className="btn primary"
          style={{ padding: "9px 20px", display: "inline-flex", alignItems: "center", gap: 8 }}
          disabled={streaming || !input.trim()}
          onClick={() => send()}
        >
          {streaming ? "生成中…" : <>发送 <Send size={14} /></>}
        </button>
      </div>
    </div>
  );
}
