import { useState } from "react";
import useSWR, { mutate } from "swr";
import { type Draft, api } from "../api.ts";

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
      const res = await api.updateDraftText(d.draft_id, text);
      if (!res.saved) {
        setSaveError(
          `Numbers not in facts_json: ${res.digit_audit_errors.join(", ")}`,
        );
      } else {
        await mutate("drafts");
      }
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

      {d.has_card ? (
        <img
          src={`/api/candidates/${d.candidate_id}/card.png`}
          alt="stat card"
          className="card-preview"
        />
      ) : (
        <div
          className="card-placeholder"
          style={{ aspectRatio: "16/9", marginBottom: 16 }}
        >
          <span>Card not available</span>
        </div>
      )}

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
          title={!canApprove ? "Draft must be verified and saved before approving" : ""}
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
          style={{ marginTop: 12, fontSize: 12, color: "var(--text-secondary)" }}
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

  const selectedItem = sorted.find((d) => d.draft_id === selected) ?? null;

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
