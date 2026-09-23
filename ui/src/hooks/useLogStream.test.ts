import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

import { useLogStream } from "./useLogStream";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static status: "open" | "error" = "open";
  static messages: string[] = [];
  static failFirst = 0;

  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  static reset() {
    FakeEventSource.instances = [];
    FakeEventSource.status = "open";
    FakeEventSource.messages = [];
    FakeEventSource.failFirst = 0;
  }

  static latest() {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1];
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useLogStream", () => {
  it("connects and marks finished on a terminal status event", async () => {
    const { result } = renderHook(() => useLogStream({ jobName: "a" }));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.latest().url).toContain("/api/jobs/a/stream");
    expect(result.current.streamStatus).toBe("connecting");

    act(() => {
      FakeEventSource.latest().onopen?.();
      FakeEventSource.latest().emit({ type: "log", level: "info", message: "started" });
    });
    expect(result.current.streamStatus).toBe("connected");
    expect(result.current.isRunning).toBe(true);
    expect(result.current.events).toHaveLength(1);

    act(() => {
      FakeEventSource.latest().emit({ type: "status", status: "success" });
    });
    expect(result.current.streamStatus).toBe("finished");
    expect(result.current.isRunning).toBe(false);
    expect(FakeEventSource.latest().closed).toBe(true);
    expect(result.current.latestStatus).toBe("success");
  });

  it("treats idle as terminal so the UI stops waiting", async () => {
    const { result } = renderHook(() => useLogStream({ jobName: "b" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    act(() => {
      FakeEventSource.latest().onopen?.();
      FakeEventSource.latest().emit({ type: "status", status: "idle" });
    });
    expect(result.current.streamStatus).toBe("finished");
    expect(result.current.isRunning).toBe(false);
  });

  it("resubscribes when clear() is called after a run starts", async () => {
    const { result } = renderHook(() => useLogStream({ jobName: "c" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    act(() => {
      FakeEventSource.latest().onopen?.();
      FakeEventSource.latest().emit({ type: "status", status: "idle" });
    });
    expect(FakeEventSource.instances).toHaveLength(1);

    act(() => {
      result.current.clear();
    });
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(2));
    expect(result.current.events).toHaveLength(0);
    expect(result.current.streamStatus).toBe("connecting");

    act(() => {
      FakeEventSource.latest().onopen?.();
      FakeEventSource.latest().emit({ type: "log", level: "info", message: "Job 'c' started" });
    });
    expect(result.current.events).toHaveLength(1);
  });

  it("stops after repeated transport errors", async () => {
    const { result } = renderHook(() => useLogStream({ jobName: "d" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    for (let i = 0; i < 3; i += 1) {
      act(() => {
        FakeEventSource.latest().onerror?.();
      });
    }
    expect(result.current.streamStatus).toBe("error");
    expect(result.current.isRunning).toBe(false);
    expect(FakeEventSource.latest().closed).toBe(true);
  });

  it("recover() forces a new subscription", async () => {
    const { result } = renderHook(() => useLogStream({ jobName: "e" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    act(() => {
      result.current.reconnect();
    });
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(2));
  });

  it("does not connect when disabled", async () => {
    renderHook(() => useLogStream({ jobName: "f", enabled: false }));
    await new Promise((r) => setTimeout(r, 10));
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});
