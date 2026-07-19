import { useState } from "react";
import useSWR from "swr";
import { api } from "../api.ts";
import { PromptPanel, type PromptRequest } from "../components/PromptPanel.tsx";
import { CopyButton, Empty, Fetch, Lane } from "../components/common.tsx";
import type { Board, BoardLead, Candidate } from "../types.ts";

function CandidateRow({
  cand,
  onPrompt,
  onChanged,
}: {
  cand: Candidate;
  onPrompt: (r: PromptRequest) => void;
  onChanged: () => void;
}) {
  const [preview, setPreview] = useState<number | null>(cand.has_card ? Date.now() : null);
  const [rendering, setRendering] = useState(false);

  const headline =
    (cand.facts.headline as string) ?? (cand.facts.framing as string) ?? cand.subject;

  return (
    <div className="finding">
      <div className="finding-top">
        <span className="finding-kind">{cand.detector}</span>
        <span className="finding-subject">{cand.subject}</span>
        <span className="finding-score">{cand.novelty_score.toFixed(2)}</span>
      </div>
      <p className="finding-headline">{headline}</p>
      <p className="finding-meta">
        {cand.claim_scope && <span className="chip">{cand.claim_scope}</span>}
        {cand.coverage_window && <span className="chip">{cand.coverage_window}</span>}
        {cand.has_draft && <span className="chip chip-warn">already drafted</span>}
      </p>

      {preview && (
        <img
          className="finding-preview"
          src={api.candidateImageUrl(cand.candidate_id, preview)}
          alt={cand.subject}
        />
      )}

      <div className="finding-actions">
        <button
          className="btn btn-solid btn-sm"
          onClick={() => onPrompt({ kind: "draft", id: cand.candidate_id })}
        >
          Open prompt
        </button>
        <button
          className="link"
          disabled={rendering}
          onClick={async () => {
            setRendering(true);
            try {
              await api.renderCandidate(cand.candidate_id);
              setPreview(Date.now());
            } finally {
              setRendering(false);
            }
          }}
        >
          {rendering ? "Rendering…" : preview ? "Re-render card" : "Preview card"}
        </button>
        <button
          className="link link-mute"
          onClick={async () => {
            await api.rejectCandidate(cand.candidate_id);
            onChanged();
          }}
        >
          Reject
        </button>
      </div>
    </div>
  );
}

function LeadRow({
  lead,
  landed,
  onPrompt,
  onChanged,
}: {
  lead: BoardLead;
  landed: boolean;
  onPrompt: (r: PromptRequest) => void;
  onChanged: () => void;
}) {
  const [showCommands, setShowCommands] = useState(false);

  const setStatus = async (status: string) => {
    await api.setLeadStatus(lead.lead_id, status);
    onChanged();
  };

  return (
    <div className={`finding${lead.status === "exploring" ? " is-exploring" : ""}`}>
      <div className="finding-top">
        <span className="finding-kind">{lead.kind}</span>
        <span className="finding-subject">{lead.subject}</span>
        <span className="finding-score">{lead.interest.toFixed(0)}</span>
      </div>
      <p className="finding-headline">{lead.headline}</p>

      <p className="finding-meta">
        {landed ? (
          <span className="chip chip-ok">story landed</span>
        ) : lead.status === "exploring" ? (
          <span className="chip chip-warn">exploring — result not back yet</span>
        ) : (
          <span className="chip">not started</span>
        )}
      </p>

      <div className="finding-actions">
        <button
          className="btn btn-solid btn-sm"
          onClick={() => {
            onPrompt({ kind: "dive", id: lead.lead_id });
            if (lead.status !== "exploring") void setStatus("exploring");
          }}
        >
          Open prompt
        </button>
        {lead.status === "exploring" && (
          <button className="link" onClick={() => setStatus("new")}>
            Stop exploring
          </button>
        )}
        <button className="link link-mute" onClick={() => setStatus("dismissed")}>
          Dismiss
        </button>
        <button className="link link-mute" onClick={() => setShowCommands(!showCommands)}>
          {showCommands ? "Hide" : "Engine commands"}
        </button>
      </div>

      {showCommands && (
        <div className="finding-cli">
          <code>{lead.explore}</code>
          <CopyButton text={lead.explore} label="Copy" className="link" />
        </div>
      )}
    </div>
  );
}

export function Triage() {
  const candidates = useSWR("candidates", () => api.candidates("new"));
  const board = useSWR<Board>("board", api.board, { refreshInterval: 6000 });
  const [prompt, setPrompt] = useState<PromptRequest | null>(null);

  const refresh = () => {
    void candidates.mutate();
    void board.mutate();
  };

  const leads = (board.data?.leads ?? []).filter((l) => l.status !== "dismissed");
  const cards = board.data?.cards ?? [];
  // A lead has paid off when a card for the same subject landed after it.
  const landedFor = (lead: BoardLead) =>
    cards.some((c) => c.subject === lead.subject && c.created_at > lead.created_at);

  return (
    <>
      <Lane
        title="Leads"
        note="Starting points. Most should die in the dive — that's the job working."
      >
        <Fetch
          data={board.data}
          error={board.error}
          isEmpty={() => leads.length === 0}
          empty={
            <Empty>
              No open leads. Run discovery from the Desk to surface threads worth pulling.
            </Empty>
          }
        >
          {() => (
            <div className="finding-list">
              {leads.map((l) => (
                <LeadRow
                  key={l.lead_id}
                  lead={l}
                  landed={landedFor(l)}
                  onPrompt={setPrompt}
                  onChanged={refresh}
                />
              ))}
            </div>
          )}
        </Fetch>
      </Lane>

      <Lane title="Candidates" note="Verified findings ranked by novelty, ready to be written up">
        <Fetch
          data={candidates.data}
          error={candidates.error}
          isEmpty={(d) => d.length === 0}
          empty={<Empty>No open candidates. Run discovery to generate some.</Empty>}
        >
          {(list) => (
            <div className="finding-list">
              {list.map((c) => (
                <CandidateRow
                  key={c.candidate_id}
                  cand={c}
                  onPrompt={setPrompt}
                  onChanged={refresh}
                />
              ))}
            </div>
          )}
        </Fetch>
      </Lane>

      {prompt && <PromptPanel request={prompt} onClose={() => setPrompt(null)} />}
    </>
  );
}
