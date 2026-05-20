import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatComposer } from "./chat-composer";

describe("ChatComposer", () => {
  it("submits on Enter without Shift", () => {
    const onSubmit = vi.fn();

    render(
      <ChatComposer
        value="show weekly GMV"
        loading={false}
        onChange={() => undefined}
        onSubmit={onSubmit}
        onCancel={() => undefined}
      />
    );

    fireEvent.keyDown(screen.getByPlaceholderText(/ask a metric/i), {
      key: "Enter",
      shiftKey: false,
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("shows cancel action while loading", () => {
    const onCancel = vi.fn();

    render(
      <ChatComposer
        value="still running"
        loading
        onChange={() => undefined}
        onSubmit={() => undefined}
        onCancel={onCancel}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
