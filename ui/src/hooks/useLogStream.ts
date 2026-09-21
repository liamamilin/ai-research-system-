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
    errorCountRef.current = 0;
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
          setStreamStatus("finished");
          es.close();
        } else if (data.type === "error") {
          setStreamStatus("error");
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

  const isRunning = enabled && (streamStatus === "connecting" || streamStatus === "connected");

  return {
    events,
    streamStatus,
    isRunning,
    clear,
    reconnect,
    latestStatus: events.filter((e) => e.type === "status").pop()?.status as string | undefined,
  };
}
