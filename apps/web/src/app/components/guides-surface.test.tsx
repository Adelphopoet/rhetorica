import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GuidesSurface } from "./guides-surface";

describe("GuidesSurface", () => {
  it("shows the guides panel header and the guide list", () => {
    render(<GuidesSurface onClose={vi.fn()} />);

    expect(screen.getByTestId("guides-surface")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Полезные инструкции" })
    ).toBeInTheDocument();

    const guideLink = screen.getByRole("link", {
      name: /Заполнение метрик в OpenMetadata/i,
    });
    expect(guideLink).toHaveAttribute(
      "href",
      "https://example.com/docs/metadata"
    );
    expect(guideLink).toHaveAttribute("target", "_blank");
    expect(guideLink).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("closes the panel from the header action and the mobile overlay", () => {
    const onClose = vi.fn();

    render(<GuidesSurface onClose={onClose} />);

    fireEvent.click(screen.getByRole("button", { name: /Скрыть/i }));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Close guides panel" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
