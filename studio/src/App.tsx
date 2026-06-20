import { useCallback, useState } from "react";
import useSWR from "swr";
import { api, type BoardCard, type BoardLead } from "./api.ts";

export default function App() {
  const { data: board, mutate } = useSWR("board", api.board, { refreshInterval: 6000 });
  const refresh = useCallback(() => void mutate(), [mutate]);

  const cards = board?.cards.filter((c) => c.status !== "dismissed") ?? [];
  const live = cards.filter((c) => c.kind === "live_moment");
  const stories = cards.filter((c) => c.kind !== "live_moment");
  const leads = board?.leads.filter((l) => l.status !== "dismissed") ?? [];

  return (
    <div className="board">
      <Header onChanged={refresh} />
      <main className="board-main">
        {live.length > 0 && (
          <Lane title="Live now" live>
            <div className="card-grid">
              {live.map((c) => (
                <Card key={c.card_id} card={c} onChanged={refresh} />
              ))}
            </div>
          </Lane>
        )}

        <Lane title="Story feed" note="Reconciled cards, ready to post">
          {stories.length === 0 ? (
            <Empty>
              No story cards yet. Run a deep dive and <code>pad story</code> to land one here.
            </Empty>
          ) : (
            <div className="card-grid">
              {stories.map((c) => (
                <Card key={c.card_id} card={c} onChanged={refresh} />
              ))}
            </div>
          )}
        </Lane>

        <Lane title="Leads" note="Starting points, not stories — Claude takes it from here">
          {leads.length === 0 ? (
            <Empty>
              No open leads. Hit <strong>Scout</strong> to surface threads worth pulling.
            </Empty>
          ) : (
            <div className="lead-list">
              {leads.map((l) => (
                <LeadRow key={l.lead_id} lead={l} onChanged={refresh} />
              ))}
            </div>
          )}
        </Lane>
      </main>
    </div>
  );
}

// ── Header with Sync / Scout actions ────────────────────────────────────────────

function Header({ onChanged }: { onChanged: () => void }) {
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [scoutMsg, setScoutMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<"sync" | "scout" | null>(null);

  const pollSync = useCallback(() => {
    const tick = async () => {
      const s = await api.syncStatus();
      if (s.running) {
        setSyncMsg("Syncing…");
        setTimeout(tick, 1500);
        return;
      }
      const failed = s.steps.filter((x) => !x.ok);
      setSyncMsg(
        s.steps.length === 0
          ? null
          : failed.length
            ? `Synced — ${failed.length} step(s) failed`
            : "Synced ✓",
      );
      setBusy(null);
      onChanged();
    };
    void tick();
  }, [onChanged]);

  const onSync = async () => {
    setBusy("sync");
    setSyncMsg("Starting…");
    try {
      await api.startSync();
      pollSync();
    } catch (e) {
      setSyncMsg((e as Error).message);
      setBusy(null);
    }
  };

  const onScout = async () => {
    setBusy("scout");
    setScoutMsg("Scouting…");
    try {
      const r = await api.runScout();
      setScoutMsg(`${r.leads.length} leads`);
      onChanged();
    } catch (e) {
      setScoutMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <header className="board-header">
      <div className="wordmark">
        xFriars<span className="wordmark-sub">board</span>
      </div>
      <div className="actions">
        {scoutMsg && <span className="action-msg">{scoutMsg}</span>}
        <button className="btn btn-ghost" onClick={onScout} disabled={busy !== null}>
          {busy === "scout" ? "Scouting…" : "Scout"}
        </button>
        {syncMsg && <span className="action-msg">{syncMsg}</span>}
        <button className="btn btn-solid" onClick={onSync} disabled={busy !== null}>
          {busy === "sync" ? "Syncing…" : "Sync data"}
        </button>
      </div>
    </header>
  );
}

// ── Lane ────────────────────────────────────────────────────────────────────────

function Lane({
  title,
  note,
  live,
  children,
}: {
  title: string;
  note?: string;
  live?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className="lane">
      <div className="lane-head">
        {live && <span className="live-dot" />}
        <h2 className="lane-title">{title}</h2>
        {note && <span className="lane-note">{note}</span>}
      </div>
      {children}
    </section>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

// ── Card ────────────────────────────────────────────────────────────────────────

function Card({ card, onChanged }: { card: BoardCard; onChanged: () => void }) {
  const [copied, setCopied] = useState(false);

  const setStatus = async (status: string) => {
    await api.setCardStatus(card.card_id, status);
    onChanged();
  };
  const copy = async () => {
    await navigator.clipboard.writeText(card.caption);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <article className={`card${card.status === "queued" ? " is-queued" : ""}`}>
      {card.has_image ? (
        <img className="card-img" src={api.cardImageUrl(card.card_id)} alt={card.title} />
      ) : (
        <div className="card-img card-img--missing">image not rendered</div>
      )}
      <div className="card-body">
        <div className="card-badges">
          {card.reconciled ? (
            <span className="badge badge-ok">reconciled ✓</span>
          ) : (
            <span className="badge badge-live">live · unofficial</span>
          )}
          <span className="badge badge-dim">{card.confidence}</span>
          {card.status === "queued" && <span className="badge badge-queued">queued</span>}
        </div>
        <h3 className="card-title">{card.title}</h3>
        <p className="card-headline">{card.headline}</p>
        {card.rank_note && <p className="card-note">{card.rank_note}</p>}
        <div className="card-actions">
          {card.status === "queued" ? (
            <button className="link" onClick={() => setStatus("new")}>
              Unqueue
            </button>
          ) : (
            <button className="link link-strong" onClick={() => setStatus("queued")}>
              Queue
            </button>
          )}
          <button className="link" onClick={copy}>
            {copied ? "Copied" : "Copy caption"}
          </button>
          <button className="link link-mute" onClick={() => setStatus("dismissed")}>
            Dismiss
          </button>
        </div>
      </div>
    </article>
  );
}

// ── Lead ────────────────────────────────────────────────────────────────────────

function LeadRow({ lead, onChanged }: { lead: BoardLead; onChanged: () => void }) {
  const [copied, setCopied] = useState(false);

  const setStatus = async (status: string) => {
    await api.setLeadStatus(lead.lead_id, status);
    onChanged();
  };
  const copyPrompt = async () => {
    await navigator.clipboard.writeText(lead.explore);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <div className={`lead${lead.status === "exploring" ? " is-exploring" : ""}`}>
      <div className="lead-top">
        <span className="lead-kind">{lead.kind}</span>
        <span className="lead-subject">{lead.subject}</span>
        <span className="lead-interest">{lead.interest.toFixed(0)}</span>
      </div>
      <p className="lead-headline">{lead.headline}</p>
      <p className="lead-explore">{lead.explore}</p>
      <div className="lead-actions">
        <button className="link link-strong" onClick={copyPrompt}>
          {copied ? "Copied" : "Copy dive prompt"}
        </button>
        {lead.status === "exploring" ? (
          <button className="link" onClick={() => setStatus("new")}>
            Stop exploring
          </button>
        ) : (
          <button className="link" onClick={() => setStatus("exploring")}>
            Exploring
          </button>
        )}
        <button className="link link-mute" onClick={() => setStatus("dismissed")}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
