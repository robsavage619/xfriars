import { useState } from "react";
import { api } from "../api.ts";
import type { BoardCard } from "../types.ts";
import { CopyButton } from "./common.tsx";

/**
 * A rendered story card with its caption shown, not hidden behind a copy button.
 *
 * The caption is half the deliverable; you cannot judge a post you can't read.
 */
export function BoardCardTile({ card, onChanged }: { card: BoardCard; onChanged: () => void }) {
  const [showCaption, setShowCaption] = useState(false);

  const setStatus = async (status: string) => {
    await api.setCardStatus(card.card_id, status);
    onChanged();
  };

  return (
    <article className={`card${card.status === "queued" ? " is-queued" : ""}`}>
      {card.has_image ? (
        <img
          className="card-img"
          src={api.cardImageUrl(card.card_id, card.created_at)}
          alt={card.title}
        />
      ) : (
        <div className="card-img card-img--missing">image not rendered</div>
      )}
      <div className="card-body">
        <div className="card-badges">
          {card.reconciled ? (
            <span className="badge badge-ok">reconciled ✓</span>
          ) : (
            <span className="badge badge-live">live · unofficial</span>
          )}
          <span className="badge badge-dim">{card.confidence}</span>
          {card.status === "queued" && <span className="badge badge-queued">queued</span>}
        </div>
        <h3 className="card-title">{card.title}</h3>
        <p className="card-headline">{card.headline}</p>
        {card.rank_note && <p className="card-note">{card.rank_note}</p>}

        {card.caption && (
          <div className="card-caption">
            <button className="caption-toggle" onClick={() => setShowCaption(!showCaption)}>
              {showCaption ? "Hide caption" : "Show caption"}
            </button>
            {showCaption && <p className="caption-text">{card.caption}</p>}
          </div>
        )}

        <div className="card-actions">
          {card.status === "queued" ? (
            <button className="link" onClick={() => setStatus("new")}>
              Unqueue
            </button>
          ) : (
            <button className="link link-strong" onClick={() => setStatus("queued")}>
              Queue
            </button>
          )}
          {card.caption && <CopyButton text={card.caption} label="Copy caption" className="link" />}
          <button className="link link-mute" onClick={() => setStatus("dismissed")}>
            Dismiss
          </button>
        </div>
      </div>
    </article>
  );
}
