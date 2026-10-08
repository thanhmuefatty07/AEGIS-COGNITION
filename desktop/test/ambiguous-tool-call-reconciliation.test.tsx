// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AmbiguousToolCallReconciliation from "../src/AmbiguousToolCallReconciliation";

afterEach(cleanup);

describe("ambiguous tool-call reconciliation", () => {
  it("requires a second explicit action and focuses the explanation first", () => {
    const onReconcile = vi.fn();
    render(<AmbiguousToolCallReconciliation canReconcile busy={false} onReconcile={onReconcile} />);

    fireEvent.click(screen.getByRole("button", { name: "I checked the target" }));

    const explanation = screen.getByText(/Continue only if you checked the external target/);
    expect(screen.getByRole("group", { name: "Confirm external action outcome" })).toBeDefined();
    expect(document.activeElement).toBe(explanation);
    expect(onReconcile).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Keep unresolved" }));
    expect(screen.queryByRole("button", { name: "Record as not performed" })).toBeNull();
    expect(onReconcile).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "I checked the target" }));
    fireEvent.click(screen.getByRole("button", { name: "Record as not performed" }));
    expect(onReconcile).toHaveBeenCalledTimes(1);
  });

  it("does not offer reconciliation while work is still active", () => {
    render(<AmbiguousToolCallReconciliation canReconcile={false} busy={false} onReconcile={vi.fn()} />);

    expect(screen.getByText(/A task is still active/)).toBeDefined();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("disables both confirmation choices while the record is being saved", () => {
    const view = render(<AmbiguousToolCallReconciliation canReconcile busy={false} onReconcile={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "I checked the target" }));
    view.rerender(<AmbiguousToolCallReconciliation canReconcile busy onReconcile={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Keep unresolved" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Recording…" }).hasAttribute("disabled")).toBe(true);
  });
});
