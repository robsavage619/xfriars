import { useEffect, useState } from "react";
import { api } from "../api.ts";
import type { Prompt } from "../types.ts";
import { CopyButton } from "./common.tsx";

export type PromptRequest =
  | { kind: "dive"; id: string }
  | { kind: "draft"; id: string }
  | { kind: "review"; id: string }
  | { kind: "hypothesis" };

const TITLES: Record<string, string> = {
  dive: "Deep dive",
  draft: "Write the post",
  review: "Referee panel",
  hypothesis: "Propose hypotheses",
};

const BLURBS: Record<string, string> = {
  dive: "Work the lead. Most should die here — that's the point.",
  draft: "Turn a verified finding into a post. Every number is already in the prompt.",
  review: "Five lenses on the argument, not the arithmetic. Any single BLOCK blocks.",
  hypothesis: "Propose metrics the registry doesn't cover. The scanner judges them, not you.",
};

function load(req: PromptRequest): Promise<Prompt> {
  switch (req.kind) {
    case "dive":
      return api.divePrompt(req.id);
    case "draft":
      return api.draftPrompt(req.id);
    case "review":
      return api.reviewPrompt(req.id);
    case "hypothesis":
      return api.hypothesisPrompt();
  }
}

/**
 * The handoff. Shows the assembled prompt, copies it in one click, and points
 * at where the answer comes back.
 */
export function PromptPanel({ request, onClose }: { request: PromptRequest; onClose: () => void }) {
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPrompt(null);
    setError(null);
    load(request)
      .then((p) => !cancelled && setPrompt(p))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [request]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <header className="sheet-head">
          <div>
            <h2 className="sheet-title">{TITLES[request.kind]}</h2>
            <p className="sheet-blurb">
              {prompt?.subject && <strong>{prompt.subject} — </strong>}
              {BLURBS[request.kind]}
            </p>
          </div>
          <div className="sheet-head-actions">
            {prompt && <CopyButton text={prompt.prompt} label="Copy prompt" className="btn btn-solid" />}
            <button className="btn btn-ghost" onClick={onClose}>
              Close
            </button>
          </div>
        </header>

        {error && <div className="empty empty--error">Couldn't build the prompt: {error}</div>}
        {!prompt && !error && <div className="empty empty--loading">Assembling…</div>}

        {prompt && (
          <>
            <ol className="handoff-steps">
              <li>Copy the prompt</li>
              <li>Run it in Claude</li>
              <li>
                Paste the JSON back in <strong>Drafts → Paste result</strong>
              </li>
            </ol>
            <pre className="prompt-body">{prompt.prompt}</pre>
          </>
        )}
      </div>
    </div>
  );
}
