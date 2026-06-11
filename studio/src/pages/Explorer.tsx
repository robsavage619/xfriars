import { useState } from "react";
import useSWR from "swr";
import { BREF_TO_MLBAM, type ExplorerResult, api } from "../api.ts";
import { PlayerPhoto, TeamLogo } from "../components/MlbAssets.tsx";

const VIEW_LABELS: Record<string, string> = {
  all_candidates: "All Candidates",
  leaderboard: "MLB Leaderboard",
  franchise_war: "Franchise WAR",
  dollar_per_war: "$/WAR by Team",
  draft_history: "Draft History",
};

const TEAM_COLS = new Set(["team_bref", "team_abbrev", "team_id"]);
const PLAYER_COLS = new Set(["mlb_id", "player_id"]);
const NAME_COLS = new Set(["name_common", "player_name"]);

function Cell({
  value,
  col,
  row,
  cols,
}: {
  value: unknown;
  col: string;
  row: unknown[];
  cols: string[];
}) {
  if (value === null)
    return <span style={{ color: "var(--text-secondary)", opacity: 0.5 }}>—</span>;

  const str = String(value);

  // Team logo + name inline
  if (TEAM_COLS.has(col) && BREF_TO_MLBAM[str.toUpperCase()]) {
    return (
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <TeamLogo brefCode={str} size={20} opacity={0.9} />
        <span>{str}</span>
      </span>
    );
  }

  // Player name with inline photo — look for sibling mlb_id / player_id column
  if (NAME_COLS.has(col)) {
    const idCol = cols.findIndex((c) => PLAYER_COLS.has(c));
    const mlbId = idCol >= 0 ? Number(row[idCol]) : NaN;
    if (!isNaN(mlbId) && mlbId > 0) {
      return (
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <PlayerPhoto mlbId={mlbId} size={28} />
          <span>{str}</span>
        </span>
      );
    }
  }

  return <>{str}</>;
}

function DataTable({ result }: { result: ExplorerResult }) {
  if (result.error) {
    return <div className="error-msg">{result.error}</div>;
  }
  if (!result.columns.length) {
    return <div className="hint">No data</div>;
  }
  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {result.columns.map((col) => (
              <th key={col}>{col.replace(/_/g, " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j}>
                  <Cell
                    value={cell}
                    col={result.columns[j]}
                    row={row}
                    cols={result.columns}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ViewPane({ view }: { view: string }) {
  const { data, error, isLoading } = useSWR(`explorer/${view}`, () =>
    api.explorerQuery(view),
  );

  if (isLoading) {
    return (
      <div className="hint">
        <span className="spinner" />
      </div>
    );
  }
  if (error) {
    return <div className="error-msg">{String(error)}</div>;
  }
  if (!data) return null;

  return (
    <div>
      <div
        style={{
          fontSize: 12,
          color: "var(--text-secondary)",
          marginBottom: 12,
        }}
      >
        {data.row_count ?? data.rows.length} rows
      </div>
      <DataTable result={data} />
    </div>
  );
}

export default function Explorer() {
  const { data: views } = useSWR("explorer/views", api.explorerViews);
  const [active, setActive] = useState<string>("all_candidates");

  const tabs = views ?? Object.keys(VIEW_LABELS);

  return (
    <div className="page-wrap">
      <div
        style={{
          fontFamily: "Barlow Condensed, sans-serif",
          fontWeight: 700,
          fontSize: 22,
          color: "var(--text-white)",
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          marginBottom: 16,
        }}
      >
        Data Explorer
      </div>
      <div className="explorer-tabs">
        {tabs.map((v) => (
          <button
            key={v}
            className={`explorer-tab${active === v ? " active" : ""}`}
            onClick={() => setActive(v)}
          >
            {VIEW_LABELS[v] ?? v.replace(/_/g, " ")}
          </button>
        ))}
      </div>
      <ViewPane key={active} view={active} />
    </div>
  );
}
