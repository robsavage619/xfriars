// X-post mock — judge the post the way followers will see it.

interface Props {
  text: string;
  imageUrl: string | null;
}

function Icon({ d }: { d: string }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <path d={d} stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const ICONS = {
  reply: "M21 11.5a8.38 8.38 0 0 1-8.5 8.5 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 8.5-8.5 8.38 8.38 0 0 1 8.5 8.5z",
  repost: "M17 1l4 4-4 4M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4M21 13v2a4 4 0 0 1-4 4H3",
  like: "M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21.2l7.8-7.8 1-1a5.5 5.5 0 0 0 0-7.8z",
  views: "M18 20V10M12 20V4M6 20v-6",
};

export default function TweetMock({ text, imageUrl }: Props) {
  return (
    <div className="tweet-mock">
      <div className="tweet-mock-head">
        <div className="tweet-avatar">xF</div>
        <div className="tweet-id">
          <span className="tweet-name">xFriars</span>
          <span className="tweet-handle">@xFriars · now</span>
        </div>
      </div>
      <div className="tweet-text">{text || "…"}</div>
      {imageUrl && <img src={imageUrl} alt="card" className="tweet-media" />}
      <div className="tweet-actions">
        <Icon d={ICONS.reply} />
        <Icon d={ICONS.repost} />
        <Icon d={ICONS.like} />
        <Icon d={ICONS.views} />
      </div>
    </div>
  );
}
