import { useState } from "react";
import "./App.css";
import EmailPanel from "./EmailPanel.jsx";
import ChatWindow from "./ChatWindow.jsx";

function App() {
  const [tab, setTab] = useState("email");

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">andSons CRM Demo</div>
        <nav className="tabs">
          <button
            type="button"
            className={tab === "email" ? "tab active" : "tab"}
            onClick={() => setTab("email")}
          >
            Email Generation
          </button>
          <button
            type="button"
            className={tab === "chat" ? "tab active" : "tab"}
            onClick={() => setTab("chat")}
          >
            Analytics Chat
          </button>
        </nav>
      </header>

      <main className="app-main">
        {tab === "email" ? <EmailPanel /> : <ChatWindow />}
      </main>
    </div>
  );
}

export default App;
