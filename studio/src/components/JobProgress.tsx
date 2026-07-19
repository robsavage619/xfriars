import type { JobState } from "../types.ts";

function when(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 24 ? `${hrs}h ago` : `${Math.round(hrs / 24)}d ago`;
}

/** Per-step progress for a running job, or the record of the last one. */
export function JobProgress({ state, label }: { state: JobState | null; label: string }) {
  if (!state || (!state.running && state.steps.length === 0)) {
    return <p className="job-idle">No {label} run recorded yet.</p>;
  }

  return (
    <div className={`job${state.running ? " is-running" : ""}`}>
      <div className="job-head">
        {state.running ? (
          <>
            <span className="live-dot" />
            <strong>Running…</strong>
          </>
        ) : (
          <strong className={state.ok === false ? "job-degraded" : ""}>
            Last {label} {when(state.finished_at)}
            {state.ok === false && " — some steps failed"}
          </strong>
        )}
      </div>

      <ul className="job-steps">
        {state.steps.map((s, i) => (
          <li
            key={`${s.name}-${i}`}
            className={s.ok === null ? "step-running" : s.ok ? "step-ok" : "step-fail"}
          >
            <span className="step-mark">{s.ok === null ? "◌" : s.ok ? "✓" : "✕"}</span>
            <span className="step-name">{s.name}</span>
            <span className="step-detail">{s.detail}</span>
          </li>
        ))}
      </ul>

      {state.summary && !state.running && <p className="job-summary">{state.summary}</p>}
    </div>
  );
}
