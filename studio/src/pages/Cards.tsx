import { type CSSProperties, useState } from "react";
import useSWR from "swr";
import { api } from "../api.ts";

const CARD_LABELS: Record<string, string> = {
  spray: "Spray chart",
  hr: "HR spray + distance",
  launch: "Launch angle / EV",
  movement: "Arsenal (movement)",
  zone: "Pitch-location zone",
  hotcold: "Hot / cold zones",
  release: "Release point",
  rolling: "Rolling xwOBA",
  swingtake: "Swing / take run value",
  batspeed: "Bat speed",
};

const QUICK_PLAYERS = [
  { id: 592518, label: "Machado (bat)" },
  { id: 650633, label: "King (pitch)" },
];

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; msg: string }
  | { kind: "ok"; n: number };

export default function Cards() {
  const { data: cards } = useSWR("spatial-cards", api.spatialCards);
  const [card, setCard] = useState("spray");
  const [player, setPlayer] = useState(592518);
  const [season, setSeason] = useState(2024);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  async function handleRender() {
    setStatus({ kind: "loading" });
    setImgUrl(null);
    try {
      const res = await api.renderSpatial(card, player, season);
      setImgUrl(api.spatialPreviewUrl(card, player, season));
      setStatus({ kind: "ok", n: res.n });
    } catch (e) {
      setStatus({ kind: "error", msg: (e as Error).message });
    }
  }

  return (
    <div style={{ display: "flex", gap: 24, alignItems: "flex-start" }}>
      <div style={{ flex: "0 0 280px" }}>
        <h2 style={{ marginTop: 0 }}>Spatial cards</h2>

        <label style={labelStyle}>Visual</label>
        <select value={card} onChange={(e) => setCard(e.target.value)} style={inputStyle}>
          {(cards ?? Object.keys(CARD_LABELS)).map((c) => (
            <option key={c} value={c}>
              {CARD_LABELS[c] ?? c}
            </option>
          ))}
        </select>

        <label style={labelStyle}>Player (MLBAM id)</label>
        <input
          type="number"
          value={player}
          onChange={(e) => setPlayer(Number(e.target.value))}
          style={inputStyle}
        />
        <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
          {QUICK_PLAYERS.map((p) => (
            <button key={p.id} onClick={() => setPlayer(p.id)} style={chipStyle}>
              {p.label}
            </button>
          ))}
        </div>

        <label style={labelStyle}>Season</label>
        <input
          type="number"
          value={season}
          onChange={(e) => setSeason(Number(e.target.value))}
          style={inputStyle}
        />

        <button
          onClick={handleRender}
          disabled={status.kind === "loading"}
          style={{ ...buttonStyle, marginTop: 16 }}
        >
          {status.kind === "loading" ? "Rendering…" : "Render card"}
        </button>

        {status.kind === "error" && (
          <p style={{ color: "var(--negative, #C4574E)", fontSize: 13, marginTop: 12 }}>
            {status.msg}
          </p>
        )}
        {status.kind === "ok" && (
          <p style={{ color: "var(--text-secondary)", fontSize: 13, marginTop: 12 }}>
            Rendered · n = {status.n}
          </p>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {imgUrl ? (
          <img
            src={imgUrl}
            alt={`${card} card`}
            style={{ width: "100%", maxWidth: 460, borderRadius: 8, display: "block" }}
          />
        ) : (
          <div
            style={{
              border: "1px dashed var(--border, rgba(0,0,0,0.15))",
              borderRadius: 8,
              padding: 48,
              textAlign: "center",
              color: "var(--text-secondary)",
              fontSize: 14,
            }}
          >
            Pick a visual + player, then render to preview.
          </div>
        )}
      </div>
    </div>
  );
}

const labelStyle: CSSProperties = {
  display: "block",
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: "0.08em",
  color: "var(--text-secondary)",
  margin: "14px 0 4px",
};
const inputStyle: CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  borderRadius: 6,
  border: "1px solid var(--border, rgba(0,0,0,0.15))",
  background: "var(--bg-panel, #fff)",
  color: "inherit",
  fontSize: 14,
};
const buttonStyle: CSSProperties = {
  width: "100%",
  padding: "10px 14px",
  borderRadius: 6,
  border: "none",
  background: "var(--accent, #2F241D)",
  color: "#fff",
  fontWeight: 600,
  cursor: "pointer",
};
const chipStyle: CSSProperties = {
  padding: "4px 10px",
  borderRadius: 999,
  border: "1px solid var(--border, rgba(0,0,0,0.15))",
  background: "transparent",
  color: "inherit",
  fontSize: 12,
  cursor: "pointer",
};
