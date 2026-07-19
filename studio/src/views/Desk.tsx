import { useState } from "react";
import useSWR from "swr";
import { api } from "../api.ts";
import { JobProgress } from "../components/JobProgress.tsx";
import { PromptPanel, type PromptRequest } from "../components/PromptPanel.tsx";
import { BoardCardTile } from "../components/BoardCardTile.tsx";
import { Fetch, Lane } from "../components/common.tsx";
import { useJob } from "../hooks/useJob.ts";
import type { Board, CoverageReport, Stats } from "../types.ts";

function StatStrip({ stats }: { stats: Stats }) {
  const cells: [string, number][] = [
    ["candidates", stats.new_candidates],
    ["leads", stats.open_leads],
    ["cards", stats.board_new],
    ["drafts", stats.queue_size],
    ["queued", stats.board_queued],
    ["posted", stats.posted_count],
  ];
  return (
    <div className="stat-strip">
      {cells.map(([label, n]) => (
        <div key={label} className="stat">
          <span className="stat-n">{n}</span>
          <span className="stat-label">{label}</span>
        </div>
      ))}
    </div>
  );
}

/** Per-domain freshness. A degraded source explains an empty run before it happens. */
function Freshness({ reports }: { reports: CoverageReport[] }) {
  const [open, setOpen] = useState(false);
  const degraded = reports.filter((r) => r.status !== "OK");

  return (
    <div className="freshness">
      <button className="freshness-toggle" onClick={() => setOpen(!open)}>
        {degraded.length === 0
          ? `Data current across ${reports.length} domains`
          : `${degraded.length} of ${reports.length} domains degraded`}
        <span className="chev">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <ul className="freshness-list">
          {reports.map((r) => (
            <li key={r.domain} className={`fresh-${r.status.toLowerCase()}`}>
              <span className="fresh-domain">{r.domain}</span>
              <span className="fresh-status">{r.status}</span>
              <span className="fresh-detail">
                {r.latest_date ? `through ${r.latest_date}` : r.reason}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Desk({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const stats = useSWR("stats", api.stats, { refreshInterval: 15000 });
  const coverage = useSWR("coverage", api.coverage);
  const board = useSWR<Board>("board", api.board, { refreshInterval: 6000 });
  const [prompt, setPrompt] = useState<PromptRequest | null>(null);
  const [more, setMore] = useState(false);
  const [scoutMsg, setScoutMsg] = useState<string | null>(null);

  const refreshAll = () => {
    void stats.mutate();
    void board.mutate();
    void coverage.mutate();
  };

  const discover = useJob("discover", refreshAll);
  const sync = useJob("sync", refreshAll);

  const live = (board.data?.cards ?? []).filter(
    (c) => c.kind === "live_moment" && c.status !== "dismissed",
  );

  return (
    <>
      <Fetch data={stats.data} error={stats.error}>
        {(s) => <StatStrip stats={s} />}
      </Fetch>

      <Lane
        title="Find something"
        note="Detectors, the scanner, and the daily briefing — no model involved"
      >
        <div className="run-row">
          <button className="btn btn-hero" onClick={discover.start} disabled={discover.running}>
            {discover.running ? "Running discovery…" : "Run discovery"}
          </button>
          <button className="btn btn-ghost" onClick={() => setPrompt({ kind: "hypothesis" })}>
            Propose hypotheses
          </button>
          <button className="link link-mute" onClick={() => setMore(!more)}>
            {more ? "Fewer options" : "More"}
          </button>
        </div>

        {discover.error && <div className="empty empty--error">{discover.error}</div>}
        <JobProgress state={discover.state} label="discovery" />

        {more && (
          <div className="more-actions">
            <div className="run-row">
              <button className="btn btn-ghost" onClick={sync.start} disabled={sync.running}>
                {sync.running ? "Syncing…" : "Sync data"}
              </button>
              <button
                className="btn btn-ghost"
                onClick={async () => {
                  setScoutMsg("Scouting…");
                  const r = await api.runScout();
                  setScoutMsg(`${r.leads.length} leads on the board`);
                  refreshAll();
                }}
              >
                Scout only
              </button>
              {scoutMsg && <span className="action-msg">{scoutMsg}</span>}
            </div>
            {sync.error && <div className="empty empty--error">{sync.error}</div>}
            <JobProgress state={sync.state} label="sync" />
          </div>
        )}

        <Fetch data={coverage.data} error={coverage.error}>
          {(reports) => <Freshness reports={reports} />}
        </Fetch>
      </Lane>

      {live.length > 0 && (
        <Lane title="Live now" live note="In-game, unofficial until the box score settles">
          <div className="card-grid">
            {live.map((c) => (
              <BoardCardTile key={c.card_id} card={c} onChanged={refreshAll} />
            ))}
          </div>
        </Lane>
      )}

      <Lane title="What's waiting" note="Where the work stands right now">
        <Fetch data={stats.data} error={stats.error}>
          {(s) => (
            <div className="next-up">
              <button className="next-item" onClick={() => onNavigate("triage")}>
                <strong>{s.new_candidates + s.open_leads}</strong> findings to triage
                <span className="next-hint">candidates and leads — open a prompt from here</span>
              </button>
              <button className="next-item" onClick={() => onNavigate("drafts")}>
                <strong>{s.queue_size + s.board_new}</strong> drafts and cards to review
                <span className="next-hint">paste Claude's results in, edit captions, approve</span>
              </button>
              <button className="next-item" onClick={() => onNavigate("shipped")}>
                <strong>{s.board_queued + s.posted_count}</strong> queued and shipped
                <span className="next-hint">post commands, engagement, prediction scorecard</span>
              </button>
            </div>
          )}
        </Fetch>
      </Lane>

      {prompt && <PromptPanel request={prompt} onClose={() => setPrompt(null)} />}
    </>
  );
}
