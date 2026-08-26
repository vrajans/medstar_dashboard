"use client";

import { useState, useRef, useEffect } from "react";
import { api, auth } from "@/lib/api";

type Msg = { role: "user" | "assistant"; content: string };

const SUGGESTIONS = [
  "How did revenue trend this year?",
  "Which client contributes the most revenue?",
  "Is my net cash flow healthy?",
];

export default function AiPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    const prior = messages;                       // history BEFORE this question
    setMessages([...messages, { role: "user", content: q }]);
    setBusy(true);
    try {
      const r = await api.chat(q, auth.tenantId, prior);
      setMessages((m) => [...m, { role: "assistant", content: r.answer }]);
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-4rem)] max-w-3xl flex-col">
      <div>
        <h1 className="text-2xl font-bold text-navy">AI Insights</h1>
        <p className="text-sm text-slate-500">Ask anything about your data — grounded in your warehouse.</p>
      </div>

      <div className="mt-4 flex-1 space-y-4 overflow-y-auto rounded-xl border border-slate-200 bg-white p-5">
        {messages.length === 0 && (
          <div className="space-y-3">
            <div className="text-sm text-slate-500">Try one of these:</div>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="rounded-full border border-slate-200 px-3 py-1.5 text-sm text-navy hover:border-brand hover:text-brand"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-right" : "text-left"}>
            <div
              className={`inline-block max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm ${
                m.role === "user" ? "bg-brand text-white" : "bg-slate-100 text-navy"
              }`}
            >
              {m.content}
            </div>
          </div>
        ))}
        {busy && <div className="text-sm text-slate-400">Thinking…</div>}
        <div ref={endRef} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="mt-3 flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about revenue, margins, clients…"
          className="flex-1 rounded-lg border border-slate-200 px-4 py-2.5 text-sm focus:border-brand focus:outline-none"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-brand px-5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
        >
          Send
        </button>
      </form>
    </div>
  );
}
