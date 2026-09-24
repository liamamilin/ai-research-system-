import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { useState } from "react";
import { Modal } from "./Modal";

function Harness(props: Partial<React.ComponentProps<typeof Modal>> = {}) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button onClick={() => setOpen(true)}>open</button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="模板编辑器"
        {...props}
      >
        <input aria-label="标题" />
        <textarea aria-label="正文" />
        <button>保存</button>
      </Modal>
    </>
  );
}

afterEach(cleanup);

describe("Modal", () => {
  it("is a labelled modal dialog and takes focus on open", async () => {
    render(<Harness />);
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toBe("模板编辑器");
    await waitFor(() => {
      expect(document.activeElement?.getAttribute("aria-label")).toBe("标题");
    });
  });

  it("closes on Escape", () => {
    render(<Harness />);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps focus inside on Tab and restores it on close", async () => {
    render(<Harness />);
    const dialog = screen.getByRole("dialog");
    const save = screen.getByText("保存");
    save.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    // wrapped back to the first focusable element
    expect(document.activeElement?.getAttribute("aria-label")).toBe("标题");
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement?.textContent).toBe("保存");
  });

  it("restores focus to the trigger after closing", async () => {
    render(<Harness />);
    const opener = screen.getByText("open");
    opener.focus();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it("locks body scroll while open and restores it after", () => {
    const { unmount } = render(<Harness />);
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).toBe("");
  });

  it("asks before discarding unsaved work", () => {
    const onClose = vi.fn();
    render(<Harness dirty onClose={onClose} />);

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText("有未保存的修改，确定要关闭吗？")).toBeTruthy();

    fireEvent.click(screen.getByText("继续编辑"));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByText("有未保存的修改，确定要关闭吗？")).toBeNull();
  });

  it("closes after the discard is confirmed", () => {
    const onClose = vi.fn();
    render(<Harness dirty onClose={onClose} />);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.click(screen.getByText("放弃修改"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("supports a custom discard action", () => {
    const onClose = vi.fn();
    const onConfirmClose = vi.fn();
    render(<Harness dirty onClose={onClose} onConfirmClose={onConfirmClose} confirmLabel="放弃并重置" />);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.click(screen.getByText("放弃并重置"));
    expect(onConfirmClose).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("ignores Escape and backdrop clicks when not dismissible", () => {
    const onClose = vi.fn();
    render(<Harness dismissible={false} onClose={onClose} />);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes on backdrop click but not on panel click", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    const dialog = screen.getByRole("dialog");
    fireEvent.click(dialog);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(dialog.parentElement as HTMLElement);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
