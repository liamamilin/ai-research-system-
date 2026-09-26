// SSE hook for streaming job logs and status updates

import { useCallback, useEffect, useRef, useState } from "react";

export type LogEvent = {
  type: "log" | "progress" | "status" | "error";
  ts?: number;
  level?: string;
  message?: string;
  status?: string;
  phase?: string;
  [key: string]: unknown;
};

export type StreamStatus = "connecting" | "connected" | "finished" | "error";

interface UseLogStreamOptions {
  jobName: string;
  enabled?: boolean;
}

// Give up after this many consecutive transport errors (e.g. task not found,
// expired session) instead of reconnecting silently forever.
const MAX_CONSECUTIVE_ERRORS = 3;

export function useLogStream({ jobName, enabled = true }: UseLogStreamOptions) {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("connecting");
  // Whether the server has told us a run is in flight. Deliberately not derived
  // from streamStatus: a stream that is merely connecting says nothing about
  // the job, and opening the log tab of an idle job used to make the page
  // claim "运行中" and hide the previous run's log.
  const [runActive, setRunActive] = useState(false);
  const [nonce, setNonce] = useState(0);
  const eventSourceRef = useRef<EventSource | null>(null);
  const errorCountRef = useRef(0);

  const addEvent = useCallback((event: LogEvent) => {
    setEvents((prev) => {
      const next = [...prev, event];
      return next.length > 2000 ? next.slice(-2000) : next;
    });
  }, []);

  const clear = useCallback(() => {
    setEvents([]);
    setStreamStatus("connecting");
    setRunActive(false);
    errorCountRef.current = 0;
    // Force a fresh subscription: the previous EventSource may have already
    // terminated (idle/finished/error), and a run started right after would
    // otherwise never be observed.
    setNonce((n) => n + 1);
  }, []);

  const reconnect = useCallback(() => {
    errorCountRef.current = 0;
    setStreamStatus("connecting");
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!enabled || !jobName) return;

    const encodedName = encodeURIComponent(jobName);
    const url = `/api/jobs/${encodedName}/stream`;

    const es = new EventSource(url, { withCredentials: true });
    eventSourceRef.current = es;

    es.onopen = () => {
      errorCountRef.current = 0;
      setStreamStatus("connected");
    };

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as LogEvent;
        addEvent(data);
        errorCountRef.current = 0;

        if (data.type === "status") {
          // "idle" means the job exists but nothing is running in this
          // process; treat it as a terminal state so the UI stops waiting.
          setStreamStatus("finished");
          setRunActive(false);
          es.close();
        } else if (data.type === "error") {
          setStreamStatus("error");
          setRunActive(false);
        } else {
          // log / progress: the server is streaming a run, so one is in flight.
          setRunActive(true);
        }
      } catch {
        // skip malformed events
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects; stop it once we know the stream is
      // truly unavailable (task not found, server down, session expired).
      errorCountRef.current += 1;
      if (errorCountRef.current >= MAX_CONSECUTIVE_ERRORS) {
        es.close();
        setStreamStatus("error");
      }
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [jobName, enabled, addEvent, nonce]);

  const isRunning = enabled && runActive;

  return {
    events,
    streamStatus,
    isRunning,
    clear,
    reconnect,
    latestStatus: events.filter((e) => e.type === "status").pop()?.status as string | undefined,
  };
}
