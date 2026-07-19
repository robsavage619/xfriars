import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.ts";
import type { JobState } from "../types.ts";

/**
 * Start and follow a named background job.
 *
 * Polls only while the job is running, and loads the last run on mount so a
 * page opened the morning after still shows what the overnight run found.
 */
export function useJob(job: "sync" | "discover", onFinished?: () => void) {
  const [state, setState] = useState<JobState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const finished = useRef(onFinished);
  finished.current = onFinished;

  const poll = useCallback(async () => {
    try {
      const s = await api.jobStatus(job);
      setState(s);
      if (s.running) {
        timer.current = window.setTimeout(() => void poll(), 1200);
      } else {
        finished.current?.();
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, [job]);

  useEffect(() => {
    void poll();
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [poll]);

  const start = useCallback(async () => {
    setError(null);
    try {
      const r = await api.startJob(job);
      if (!r.started) {
        setError(r.blocked_by ? `${r.blocked_by} is already running` : "Already running");
        return;
      }
      void poll();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [job, poll]);

  return { state, error, start, running: state?.running ?? false };
}
