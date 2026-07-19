import { useState } from "react";
import { api } from "../api.ts";
import type { GateOutcome } from "../types.ts";

/**
 * Where Claude's answer comes back in.
 *
 * The server decides what the payload is and which gates it faces; this only
 * reports the verdict, gate by gate, so a rejection says which one refused it
 * rather than "something went wrong".
 */
export function ResultPastebox({ onLanded }: { onLanded: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<GateOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    setOutcome(null);
    try {
      const result = await api.landResult(text);
      setOutcome(result);
      if (result.accepted) {
        setText("");
        onLanded();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pastebox">
      <textarea
        className="pastebox-input"
        placeholder="Paste Claude's JSON here — a draft, referee verdicts, hypotheses, or an honest no_story."
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={5}
      />
      <div className="pastebox-actions">
        <button className="btn btn-solid" onClick={submit} disabled={busy || !text.trim()}>
          {busy ? "Running the gates…" : "Land result"}
        </button>
        {text && (
          <button className="link link-mute" onClick={() => setText("")}>
            Clear
          </button>
        )}
      </div>

      {error && <div className="gate-report gate-report--bad">{error}</div>}

      {outcome && (
        <div className={`gate-report ${outcome.accepted ? "gate-report--ok" : "gate-report--bad"}`}>
          <div className="gate-summary">
            <span className="badge badge-dim">{outcome.kind}</span>
            {outcome.summary}
          </div>
          {outcome.gates.length > 0 && (
            <ul className="gate-list">
              {outcome.gates.map((g) => (
                <li key={g.name} className={g.ok ? "gate-ok" : "gate-fail"}>
                  <span className="gate-name">{g.ok ? "✓" : "✕"} {g.name.replace(/_/g, " ")}</span>
                  <span className="gate-detail">{g.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
