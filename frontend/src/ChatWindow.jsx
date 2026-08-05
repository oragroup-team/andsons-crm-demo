import { useState, useRef, useEffect } from "react";
import { askQuestion } from "./api";

const SUGGESTIONS = [
  "How many orders has andSons had in Singapore?",
  "What's our total final revenue from delivered orders in Singapore?",
  "How much have we spent on hair loss marketing in Singapore?",
];

export default function ChatWindow() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send(question) {
    const q = question ?? input;
    if (!q.trim() || loading) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text: q }]);
    setLoading(true);
    try {
      const data = await askQuestion(q);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: data.answer,
          sqlQuery: data.sql_query,
          verified: data.verified,
          dataSource: data.data_source,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: `Error: ${err.message}`, error: true },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    send();
  }

  return (
    <div className="panel chat-panel">
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p>Ask a question about customers, emails, opens, clicks, or orders.</p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-message ${m.role}`}>
            <div className="chat-bubble">
              <div className="chat-text">{m.text}</div>
              {m.role === "assistant" && !m.error && (
                <>
                  <div className={`verified-badge ${m.verified ? "verified" : "unverified"}`}>
                    {m.verified ? "✓ grounded in query result" : "⚠ unverified"}
                  </div>
                  {m.dataSource && (
                    <div className="data-source-badge">Source: live BigQuery</div>
                  )}
                  {m.sqlQuery && (
                    <details className="sql-details">
                      <summary>SQL query</summary>
                      <pre className="sql-block">{m.sqlQuery}</pre>
                    </details>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="chat-message assistant">
            <div className="chat-bubble chat-loading">Thinking…</div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form className="chat-input-row" onSubmit={handleSubmit}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask an analytics question…"
        />
        <button type="submit" disabled={loading || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
