import { useState, type ReactNode } from "react";

/** Section wrapper with a title and optional note. */
export function Lane({
  title,
  note,
  live,
  right,
  children,
}: {
  title: string;
  note?: string;
  live?: boolean;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="lane">
      <div className="lane-head">
        {live && <span className="live-dot" />}
        <h2 className="lane-title">{title}</h2>
        {note && <span className="lane-note">{note}</span>}
        {right && <div className="lane-right">{right}</div>}
      </div>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/**
 * Loading / error / empty around SWR state.
 *
 * A failed fetch used to render as an empty lane, which reads as "nothing
 * found" — the opposite of what happened.
 */
export function Fetch<T>({
  data,
  error,
  isEmpty,
  empty,
  children,
}: {
  data: T | undefined;
  error: unknown;
  isEmpty?: (d: T) => boolean;
  empty?: ReactNode;
  children: (d: T) => ReactNode;
}) {
  if (error) {
    return <div className="empty empty--error">Couldn't load: {(error as Error).message}</div>;
  }
  if (data === undefined) return <div className="empty empty--loading">Loading…</div>;
  if (isEmpty?.(data)) return <>{empty ?? <Empty>Nothing here yet.</Empty>}</>;
  return <>{children(data)}</>;
}

/** A button that copies text and confirms it did. */
export function CopyButton({
  text,
  label = "Copy",
  className = "link link-strong",
}: {
  text: string;
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className={className}
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1400);
      }}
    >
      {copied ? "Copied ✓" : label}
    </button>
  );
}
