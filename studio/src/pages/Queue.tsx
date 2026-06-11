import { useCallback, useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import { type Draft, api } from "../api.ts";
import TweetMock from "../components/TweetMock.tsx";

const STATUS_ORDER: Record<string, number> = {
  approved: 0,
  verified: 1,
  pending: 2,
};

function DraftDetail({ d }: { d: Draft }) {
  const [text, setText] = useState(d.text);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  const isDirty = text !== d.text;
  const isOver = text.length > 280;

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      await api.updateDraftText(d.draft_id, text);
      await mutate("drafts");
    } catch (e) {
      setSaveError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleApprove() {
    setActing(true);
    try {
      await api.approveDraft(d.draft_id);
      await mutate("drafts");
      await mutate("stats");
    } catch (e) {
      alert(String(e));
    } finally {
      setActing(false);
    }
  }

  async function handleReject() {
    if (!confirm("Reject this draft?")) return;
    setActing(true);
    try {
      await api.rejectDraft(d.draft_id);
      await mutate("drafts");
      await mutate("stats");
    } catch (e) {
      alert(String(e));
    } finally {
      setActing(false);
    }
  }

  const canApprove = d.status === "verified" && !isDirty;
  const canReject = !["posted", "rejected"].includes(d.status);

  return (
    <div>
      <div className="detail-title">{d.detector.replace(/_/g, " ")}</div>
      <div className="detail-sub">
        <span className={`status-badge status-${d.status}`}>{d.status}</span>
        {" · "}
        {d.claim_scope}
        {d.is_projection && (
          <span
            style={{
              marginLeft: 8,
              fontSize: 10,
              color: "var(--gold)",
              border: "1px solid var(--gold)",
              padding: "1px 4px",
              borderRadius: 3,
            }}
          >
            PROJECTION
          </span>
        )}
      </div>

      {d.interesting_judgment && (
        <div className="judgment-box">{d.interesting_judgment}</div>
      )}

      <div className="detail-section">
        <div className="detail-label">Post Preview</div>
        <TweetMock
          text={text}
          imageUrl={
            d.has_card ? `/api/candidates/${d.candidate_id}/card.png` : null
          }
        />
      </div>

      <div className="detail-section">
        <div className="detail-label">Caption</div>
        <textarea
          className={`caption-area${saveError ? " error" : ""}`}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setSaveError(null);
          }}
          rows={4}
        />
        <div className={`char-counter${isOver ? " over" : ""}`}>
          {text.length} / 280
        </div>
        {saveError && <div className="audit-error">{saveError}</div>}
        {isDirty && (
          <div className="btn-row">
            <button
              className="btn btn-primary btn-sm"
              onClick={handleSave}
              disabled={saving || isOver}
            >
              {saving ? <span className="spinner" /> : "Save"}
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setText(d.text);
                setSaveError(null);
              }}
            >
              Discard
            </button>
          </div>
        )}
      </div>

      <hr className="divider" />

      <div className="btn-row">
        <button
          className="btn btn-primary"
          onClick={handleApprove}
          disabled={!canApprove || acting}
          title={
            !canApprove
              ? "Draft must be verified and saved before approving"
              : ""
          }
        >
          {acting ? <span className="spinner" /> : "Approve"}
        </button>
        {canReject && (
          <button
            className="btn btn-danger"
            onClick={handleReject}
            disabled={acting}
          >
            Reject
          </button>
        )}
      </div>

      {d.status === "approved" && (
        <div
          style={{
            marginTop: 12,
            fontSize: 12,
            color: "var(--text-secondary)",
          }}
        >
          Ready to post. Run:{" "}
          <code style={{ color: "var(--gold)" }}>
            uv run pad post {d.draft_id}
          </code>
        </div>
      )}
    </div>
  );
}

export default function Queue() {
  const [selected, setSelected] = useState<string | null>(null);
  const { data, error } = useSWR(
    "drafts",
    () => api.drafts("pending,verified,approved"),
    { refreshInterval: 5000 },
  );

  const sorted = [...(data ?? [])].sort(
    (a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9),
  );

  const selectedIdx = sorted.findIndex((d) => d.draft_id === selected);
  const selectedItem = selectedIdx >= 0 ? sorted[selectedIdx] : null;

  const move = useCallback(
    (delta: number) => {
      if (!sorted.length) return;
      const next =
        selectedIdx < 0
          ? 0
          : Math.min(Math.max(selectedIdx + delta, 0), sorted.length - 1);
      setSelected(sorted[next].draft_id);
    },
    [sorted, selectedIdx],
  );

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
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move]);

  return (
    <div className="split">
      <div className="split-list">
        <div className="list-header">
          {data
            ? `${data.length} draft${data.length !== 1 ? "s" : ""}`
            : "Loading…"}
        </div>
        {error && (
          <div className="error-msg" style={{ margin: 12 }}>
            {String(error)}
          </div>
        )}
        {sorted.map((d) => (
          <div
            key={d.draft_id}
            className={`list-item${selected === d.draft_id ? " selected" : ""}`}
            onClick={() => setSelected(d.draft_id)}
          >
            <div className="list-item-top">
              <span className="detector-name">
                {d.detector.replace(/_/g, " ")}
              </span>
              <span className={`status-badge status-${d.status}`}>
                {d.status}
              </span>
            </div>
            <div className="list-item-sub">
              {d.char_count}/280 chars · score {d.novelty_score.toFixed(2)}
            </div>
            <div className="list-item-headline">
              {d.text.slice(0, 100)}
              {d.text.length > 100 ? "…" : ""}
            </div>
          </div>
        ))}
        {data?.length === 0 && (
          <div className="hint">
            Queue is empty.
            <br />
            Run /padres-stat in Claude Code to generate drafts.
          </div>
        )}
        <div className="kbd-bar">
          <span>
            <kbd>J</kbd>
            <kbd>K</kbd>navigate
          </span>
        </div>
      </div>
      <div className="split-detail">
        {selectedItem ? (
          <DraftDetail key={selectedItem.draft_id} d={selectedItem} />
        ) : (
          <div className="detail-empty">Select a draft to review</div>
        )}
      </div>
    </div>
  );
}
