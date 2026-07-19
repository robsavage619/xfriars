import useSWR from "swr";
import { api } from "../api.ts";
import { BoardCardTile } from "../components/BoardCardTile.tsx";
import { CopyButton, Empty, Fetch, Lane } from "../components/common.tsx";
import type { Board, Draft, PostedItem, Predictions } from "../types.ts";

/** Posting stays on the command line on purpose — the app hands you the command. */
function ApprovedRow({ draft }: { draft: Draft }) {
  const cmd = `uv run pad post --live ${draft.draft_id}`;
  return (
    <div className="finding">
      <div className="finding-top">
        <span className="finding-kind">approved</span>
        <span className="finding-subject">{draft.detector}</span>
      </div>
      <p className="finding-headline">{draft.text}</p>
      <div className="finding-cli">
        <code>{cmd}</code>
        <CopyButton text={cmd} label="Copy command" className="link" />
      </div>
    </div>
  );
}

function Scorecard({ data }: { data: Predictions }) {
  const s = data.scorecard as Record<string, number | string | null>;
  const cells = ["correct", "incorrect", "open", "accuracy"].filter((k) => k in s);

  // Nothing graded yet reads as an em dash, not "null" — an empty scorecard is
  // a real state (no calls have come due), not a missing value.
  const show = (k: string): string => {
    const v = s[k];
    if (v === null || v === undefined) return "—";
    if (k === "accuracy") return typeof v === "number" ? `${Math.round(v * 100)}%` : "—";
    return String(v);
  };

  return (
    <>
      <div className="stat-strip">
        {cells.map((k) => (
          <div key={k} className="stat">
            <span className="stat-n">{show(k)}</span>
            <span className="stat-label">{k}</span>
          </div>
        ))}
      </div>
      {data.recent.length > 0 && (
        <ul className="prediction-list">
          {data.recent.slice(0, 15).map((p) => (
            <li key={p.prediction_id} className={`pred pred--${p.outcome ?? "open"}`}>
              <span className="pred-outcome">{p.outcome ?? "open"}</span>
              <span className="pred-claim">{p.claim}</span>
              <span className="pred-when">{p.resolves_by ?? ""}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function PostedRow({ item }: { item: PostedItem }) {
  const metrics: [string, number | null][] = [
    ["impressions", item.impressions],
    ["likes", item.likes],
    ["reposts", item.reposts],
    ["replies", item.replies],
  ];
  const known = metrics.filter(([, v]) => v !== null);

  return (
    <div className="finding">
      <div className="finding-top">
        <span className="finding-kind">{item.detector ?? "posted"}</span>
        <span className="finding-subject">{item.subject ?? ""}</span>
        <span className="finding-score">{item.posted_at?.slice(0, 10) ?? ""}</span>
      </div>
      <p className="finding-headline">{item.text}</p>
      {known.length > 0 ? (
        <p className="finding-meta">
          {known.map(([label, v]) => (
            <span key={label} className="chip">
              {v?.toLocaleString()} {label}
            </span>
          ))}
        </p>
      ) : (
        <p className="finding-meta">
          <span className="chip">
            no engagement recorded — run <code>pad metrics record</code>
          </span>
        </p>
      )}
    </div>
  );
}

export function Shipped() {
  const board = useSWR<Board>("board", api.board, { refreshInterval: 6000 });
  const approved = useSWR("approved", () => api.drafts("approved"));
  const posted = useSWR("posted", api.posted);
  const predictions = useSWR("predictions", api.predictions);

  const queued = (board.data?.cards ?? []).filter((c) => c.status === "queued");

  return (
    <>
      <Lane title="Queued cards" note="Approved by you, waiting to go out">
        <Fetch
          data={board.data}
          error={board.error}
          isEmpty={() => queued.length === 0}
          empty={<Empty>Nothing queued. Queue a card from Drafts.</Empty>}
        >
          {() => (
            <div className="card-grid">
              {queued.map((c) => (
                <BoardCardTile key={c.card_id} card={c} onChanged={() => void board.mutate()} />
              ))}
            </div>
          )}
        </Fetch>
      </Lane>

      <Lane title="Ready to post" note="Posting runs from the terminal — copy the command">
        <Fetch
          data={approved.data}
          error={approved.error}
          isEmpty={(d) => d.length === 0}
          empty={<Empty>No approved drafts waiting.</Empty>}
        >
          {(list) => (
            <div className="finding-list">
              {list.map((d) => (
                <ApprovedRow key={d.draft_id} draft={d} />
              ))}
            </div>
          )}
        </Fetch>
      </Lane>

      <Lane title="Scorecard" note="The account's batting average on its own calls">
        <Fetch data={predictions.data} error={predictions.error}>
          {(d) => <Scorecard data={d} />}
        </Fetch>
      </Lane>

      <Lane title="Posted" note="What shipped, and how it did">
        <Fetch
          data={posted.data}
          error={posted.error}
          isEmpty={(d) => d.length === 0}
          empty={<Empty>Nothing posted yet.</Empty>}
        >
          {(list) => (
            <div className="finding-list">
              {list.map((p) => (
                <PostedRow key={p.draft_id} item={p} />
              ))}
            </div>
          )}
        </Fetch>
      </Lane>
    </>
  );
}
