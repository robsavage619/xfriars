import { useState } from "react";
import useSWR from "swr";
import { api } from "../api.ts";
import { BoardCardTile } from "../components/BoardCardTile.tsx";
import { PromptPanel, type PromptRequest } from "../components/PromptPanel.tsx";
import { ResultPastebox } from "../components/ResultPastebox.tsx";
import { Empty, Fetch, Lane } from "../components/common.tsx";
import type { Board, Draft, Referee } from "../types.ts";

function RefereePanel({ referee, onReview }: { referee: Referee | null; onReview: () => void }) {
  const [open, setOpen] = useState(false);

  if (!referee) {
    return (
      <div className="referee referee--none">
        <span>Not reviewed — the panel has to run before this can be approved.</span>
        <button className="link link-strong" onClick={onReview}>
          Open review prompt
        </button>
      </div>
    );
  }

  const cls = referee.stale
    ? "referee--stale"
    : referee.outcome === "cleared"
      ? "referee--cleared"
      : "referee--blocked";

  return (
    <div className={`referee ${cls}`}>
      <div className="referee-head">
        <span className="referee-outcome">
          {referee.stale ? "clearance stale" : referee.outcome}
        </span>
        {referee.stale && (
          <span className="referee-why">
            the caption changed after the panel ran — re-review before approving
          </span>
        )}
        {referee.failure_modes.length > 0 && (
          <span className="referee-modes">{referee.failure_modes.join(", ")}</span>
        )}
        <button className="link link-mute" onClick={() => setOpen(!open)}>
          {open ? "Hide lenses" : "Lenses"}
        </button>
        <button className="link" onClick={onReview}>
          Re-review
        </button>
      </div>
      {open && (
        <ul className="lens-list">
          {referee.lenses.map((l) => (
            <li key={l.lens} className={`lens lens--${l.verdict.toLowerCase()}`}>
              <span className="lens-name">{l.lens}</span>
              <span className="lens-verdict">{l.verdict}</span>
              <span className="lens-evidence">
                {l.failure_mode ? `${l.failure_mode} — ` : ""}
                {l.evidence}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DraftEditor({
  draft,
  onPrompt,
  onChanged,
}: {
  draft: Draft;
  onPrompt: (r: PromptRequest) => void;
  onChanged: () => void;
}) {
  const [text, setText] = useState(draft.text);
  const [saving, setSaving] = useState(false);
  const [gateError, setGateError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const dirty = text !== draft.text;
  const cleared = draft.referee?.outcome === "cleared" && !draft.referee.stale;
  const canApprove = draft.status === "verified" && cleared;

  const save = async () => {
    setSaving(true);
    setGateError(null);
    try {
      await api.updateDraft(draft.draft_id, text);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
      onChanged();
    } catch (e) {
      // The digit audit refused it. Show what it caught and put back the text
      // the server still holds — a rejected edit must never look applied.
      setGateError((e as Error).message);
      setText(draft.text);
    } finally {
      setSaving(false);
    }
  };

  return (
    <article className={`draft draft--${draft.status}`}>
      <div className="draft-head">
        <span className={`badge ${draft.status === "verified" ? "badge-ok" : "badge-dim"}`}>
          {draft.status}
        </span>
        {draft.status !== "verified" && draft.status !== "approved" && (
          <span className="badge badge-warn">not postable</span>
        )}
        {draft.is_projection && <span className="badge badge-dim">projection</span>}
        <span className="draft-detector">{draft.detector}</span>
      </div>

      {draft.has_card && (
        <img
          className="draft-img"
          src={api.candidateImageUrl(draft.candidate_id)}
          alt={draft.candidate_id}
        />
      )}

      <textarea
        className="draft-text"
        value={text}
        rows={3}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="draft-meta">
        <span className={text.length > 280 ? "over" : ""}>{text.length}/280</span>
        {dirty && (
          <button className="link link-strong" onClick={save} disabled={saving}>
            {saving ? "Checking digits…" : "Save"}
          </button>
        )}
        {dirty && (
          <button className="link link-mute" onClick={() => setText(draft.text)}>
            Revert
          </button>
        )}
        {saved && <span className="action-msg">Saved ✓</span>}
      </div>

      {gateError && <div className="gate-report gate-report--bad">{gateError}</div>}

      {draft.interesting_judgment && (
        <p className="draft-judgment">“{draft.interesting_judgment}”</p>
      )}

      <RefereePanel
        referee={draft.referee}
        onReview={() => onPrompt({ kind: "review", id: draft.draft_id })}
      />

      <div className="draft-actions">
        <button
          className="btn btn-solid btn-sm"
          disabled={!canApprove}
          title={canApprove ? "" : "Needs a current referee clearance and verified status"}
          onClick={async () => {
            await api.approveDraft(draft.draft_id);
            onChanged();
          }}
        >
          Approve
        </button>
        <button
          className="link link-mute"
          onClick={async () => {
            await api.rejectDraft(draft.draft_id);
            onChanged();
          }}
        >
          Reject
        </button>
      </div>
    </article>
  );
}

export function Drafts() {
  const drafts = useSWR("drafts", () => api.drafts("pending,verified,approved"));
  const board = useSWR<Board>("board", api.board, { refreshInterval: 6000 });
  const [prompt, setPrompt] = useState<PromptRequest | null>(null);

  const refresh = () => {
    void drafts.mutate();
    void board.mutate();
  };

  const stories = (board.data?.cards ?? []).filter(
    (c) => c.kind !== "live_moment" && c.status === "new",
  );

  return (
    <>
      <Lane title="Paste result" note="Claude's answer comes back here and runs the gates">
        <ResultPastebox onLanded={refresh} />
      </Lane>

      <Lane title="Drafts" note="Caption, referee verdict, and the approval gate">
        <Fetch
          data={drafts.data}
          error={drafts.error}
          isEmpty={(d) => d.length === 0}
          empty={
            <Empty>
              No drafts yet. Open a prompt from <strong>Triage</strong>, run it, and paste the
              result above.
            </Empty>
          }
        >
          {(list) => (
            <div className="draft-list">
              {list.map((d) => (
                <DraftEditor
                  key={d.draft_id}
                  draft={d}
                  onPrompt={setPrompt}
                  onChanged={refresh}
                />
              ))}
            </div>
          )}
        </Fetch>
      </Lane>

      <Lane title="Story cards" note="Rendered and reconciled by the engine — queue what ships">
        <Fetch
          data={board.data}
          error={board.error}
          isEmpty={() => stories.length === 0}
          empty={<Empty>No new story cards. Run discovery to land one.</Empty>}
        >
          {() => (
            <div className="card-grid">
              {stories.map((c) => (
                <BoardCardTile key={c.card_id} card={c} onChanged={refresh} />
              ))}
            </div>
          )}
        </Fetch>
      </Lane>

      {prompt && <PromptPanel request={prompt} onClose={() => setPrompt(null)} />}
    </>
  );
}
