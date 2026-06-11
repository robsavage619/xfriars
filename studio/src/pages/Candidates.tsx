import { useCallback, useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import { type Candidate, api } from "../api.ts";

function scoreClass(s: number) {
  if (s >= 0.85) return "score-high";
  if (s >= 0.65) return "score-mid";
  return "score-low";
}

function ScoreBadge({ score }: { score: number }) {
  return (
    <span className={`score-badge ${scoreClass(score)}`}>
      {score.toFixed(2)}
    </span>
  );
}

function FactsGrid({ facts }: { facts: Record<string, unknown> }) {
  const skip = new Set(["rows", "columns", "headline", "claim_scope"]);
  const entries = Object.entries(facts).filter(([k]) => !skip.has(k));
  if (!entries.length) return null;
  return (
    <div className="facts-grid">
      {entries.map(([k, v]) => (
        <div key={k} className="fact-cell">
          <div className="fact-key">{k.replace(/_/g, " ")}</div>
          <div className="fact-val">{String(v)}</div>
        </div>
      ))}
    </div>
  );
}

function CandidateDetail({
  c,
  onReject,
  rejecting,
}: {
  c: Candidate;
  onReject: () => void;
  rejecting: boolean;
}) {
  const [rendering, setRendering] = useState(false);
  const [imgKey, setImgKey] = useState(0);
  const [copied, setCopied] = useState(false);

  const headline =
    typeof c.facts.headline === "string" ? c.facts.headline : null;

  async function handleRender() {
    setRendering(true);
    try {
      await api.renderCard(c.candidate_id);
      setImgKey((k) => k + 1);
      await mutate("candidates");
    } catch (e) {
      alert(String(e));
    } finally {
      setRendering(false);
    }
  }

  function copyCmd() {
    const cmd = `/padres-stat ${c.candidate_id}`;
    navigator.clipboard.writeText(cmd).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  const hasCard = c.has_card || imgKey > 0;

  return (
    <div>
      <div className="detail-title">{c.detector.replace(/_/g, " ")}</div>
      <div className="detail-sub">
        {c.claim_scope} · as of {c.as_of}
      </div>

      {headline && (
        <div className="judgment-box" style={{ marginBottom: 16 }}>
          {headline}
        </div>
      )}

      {hasCard ? (
        <img
          key={imgKey}
          src={api.cardUrl(c.candidate_id)}
          alt="stat card"
          className="card-preview"
        />
      ) : (
        <div className="card-placeholder">
          <span>Card not rendered yet</span>
          <button
            className="btn btn-ghost btn-sm"
            onClick={handleRender}
            disabled={rendering}
          >
            {rendering ? <span className="spinner" /> : "Render Card"}
          </button>
        </div>
      )}

      <div className="btn-row" style={{ marginBottom: 20 }}>
        {hasCard && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={handleRender}
            disabled={rendering}
          >
            {rendering ? <span className="spinner" /> : "Re-render"}
          </button>
        )}
        <button
          className="btn btn-danger btn-sm"
          onClick={onReject}
          disabled={rejecting}
        >
          {rejecting ? <span className="spinner" /> : "Reject"}
        </button>
      </div>

      <FactsGrid facts={c.facts} />

      <div className="detail-section">
        <div className="detail-label">Generate Draft</div>
        {c.has_draft ? (
          <p style={{ fontSize: 13, color: "var(--positive)" }}>
            Draft exists — check the Queue tab.
          </p>
        ) : (
          <>
            <p
              style={{
                fontSize: 12,
                color: "var(--text-secondary)",
                marginBottom: 8,
              }}
            >
              Run this in Claude Code to generate a caption:
            </p>
            <div className="cli-block" onClick={copyCmd} title="Click to copy">
              <code>/padres-stat {c.candidate_id}</code>
              <span className="cli-copy-hint">
                {copied ? "Copied!" : "click to copy"}
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function Candidates() {
  const [selected, setSelected] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const { data, error } = useSWR("candidates", () => api.candidates("new"), {
    refreshInterval: 8000,
  });

  const items = data ?? [];
  const selectedIdx = items.findIndex((c) => c.candidate_id === selected);
  const selectedItem = selectedIdx >= 0 ? items[selectedIdx] : null;

  const move = useCallback(
    (delta: number) => {
      if (!items.length) return;
      const next = selectedIdx < 0 ? 0 : Math.min(Math.max(selectedIdx + delta, 0), items.length - 1);
      setSelected(items[next].candidate_id);
    },
    [items, selectedIdx],
  );

  const rejectSelected = useCallback(async () => {
    if (!selectedItem || rejecting) return;
    setRejecting(true);
    try {
      await api.rejectCandidate(selectedItem.candidate_id);
      // advance selection to the next survivor before refetch
      const survivors = items.filter(
        (c) => c.candidate_id !== selectedItem.candidate_id,
      );
      setSelected(
        survivors[Math.min(selectedIdx, survivors.length - 1)]?.candidate_id ??
          null,
      );
      await mutate("candidates");
      await mutate("stats");
    } catch (e) {
      alert(String(e));
    } finally {
      setRejecting(false);
    }
  }, [selectedItem, rejecting, items, selectedIdx]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if (t.tagName === "TEXTAREA" || t.tagName === "INPUT") return;
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        move(1);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        move(-1);
      } else if (e.key === "x") {
        void rejectSelected();
      } else if (e.key === "c" && selectedItem && !e.metaKey && !e.ctrlKey) {
        void navigator.clipboard.writeText(
          `/padres-stat ${selectedItem.candidate_id}`,
        );
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move, rejectSelected, selectedItem]);

  return (
    <div className="split">
      <div className="split-list">
        <div className="list-header">
          {data
            ? `${data.length} new candidate${data.length !== 1 ? "s" : ""}`
            : "Loading…"}
        </div>
        {error && (
          <div className="error-msg" style={{ margin: 12 }}>
            {String(error)}
          </div>
        )}
        {items.map((c) => (
          <div
            key={c.candidate_id}
            className={`list-item${selected === c.candidate_id ? " selected" : ""}`}
            onClick={() => setSelected(c.candidate_id)}
          >
            <div className="list-item-top">
              <span className="detector-name">
                {c.detector.replace(/_/g, " ")}
              </span>
              <ScoreBadge score={c.novelty_score} />
              {c.has_draft && (
                <span
                  className="status-badge status-verified"
                  style={{ fontSize: 9 }}
                >
                  drafted
                </span>
              )}
            </div>
            <div className="list-item-sub">{c.as_of}</div>
            {typeof c.facts.headline === "string" && (
              <div className="list-item-headline">{c.facts.headline}</div>
            )}
          </div>
        ))}
        {data?.length === 0 && (
          <div className="hint">
            No new candidates.
            <br />
            Run: uv run pad detect run
          </div>
        )}
        <div className="kbd-bar">
          <span><kbd>J</kbd><kbd>K</kbd>navigate</span>
          <span><kbd>X</kbd>reject</span>
          <span><kbd>C</kbd>copy cmd</span>
        </div>
      </div>
      <div className="split-detail">
        {selectedItem ? (
          <CandidateDetail
            c={selectedItem}
            onReject={rejectSelected}
            rejecting={rejecting}
          />
        ) : (
          <div className="detail-empty">Select a candidate to preview</div>
        )}
      </div>
    </div>
  );
}
