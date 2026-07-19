import { useEffect, useState } from "react";
import useSWR from "swr";
import { api } from "./api.ts";
import { Desk } from "./views/Desk.tsx";
import { Drafts } from "./views/Drafts.tsx";
import { Shipped } from "./views/Shipped.tsx";
import { Triage } from "./views/Triage.tsx";

const TABS = [
  { id: "desk", label: "Desk" },
  { id: "triage", label: "Triage" },
  { id: "drafts", label: "Drafts" },
  { id: "shipped", label: "Shipped" },
] as const;

type TabId = (typeof TABS)[number]["id"];

function currentTab(): TabId {
  const hash = window.location.hash.replace("#", "");
  return (TABS.find((t) => t.id === hash)?.id ?? "desk") as TabId;
}

export default function App() {
  const [tab, setTab] = useState<TabId>(currentTab);
  const stats = useSWR("stats", api.stats, { refreshInterval: 15000 });

  // The hash is the source of truth so back and reload land where you were.
  useEffect(() => {
    const onHash = () => setTab(currentTab());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = (id: string) => {
    window.location.hash = id;
    setTab(id as TabId);
  };

  const counts: Record<string, number> = {
    triage: (stats.data?.new_candidates ?? 0) + (stats.data?.open_leads ?? 0),
    drafts: (stats.data?.queue_size ?? 0) + (stats.data?.board_new ?? 0),
    shipped: (stats.data?.board_queued ?? 0) + (stats.data?.posted_count ?? 0),
  };

  return (
    <div className="board">
      <header className="board-header">
        <div className="wordmark">
          xFriars<span className="wordmark-sub">studio</span>
        </div>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`tab${tab === t.id ? " is-active" : ""}`}
              onClick={() => go(t.id)}
            >
              {t.label}
              {counts[t.id] > 0 && <span className="tab-count">{counts[t.id]}</span>}
            </button>
          ))}
        </nav>
      </header>

      <main className="board-main">
        {tab === "desk" && <Desk onNavigate={go} />}
        {tab === "triage" && <Triage />}
        {tab === "drafts" && <Drafts />}
        {tab === "shipped" && <Shipped />}
      </main>
    </div>
  );
}
