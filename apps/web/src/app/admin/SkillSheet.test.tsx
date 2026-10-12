import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SkillSheet } from "./SkillSheet";

describe("SkillSheet", () => {
  it("does not expose the removed optimizer usage or engine selectors", () => {
    render(
      <SkillSheet
        open
        onOpenChange={vi.fn()}
        editingSkill={null}
        onSubmit={vi.fn(async () => undefined)}
        saving={false}
        error={null}
      />
    );

    expect(screen.queryByRole("combobox", { name: "Назначение" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Область применения" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Ассистент по умолчанию")).toBeInTheDocument();
  });

  it("switches to manual chat skill input", () => {
    render(
      <SkillSheet
        open
        onOpenChange={vi.fn()}
        editingSkill={null}
        onSubmit={vi.fn(async () => undefined)}
        saving={false}
        error={null}
      />
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Источник" }), {
      target: { value: "manual_markdown" },
    });

    expect(screen.queryByRole("combobox", { name: "Область применения" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Ассистент по умолчанию")).toBeInTheDocument();
    expect(screen.getByLabelText("Применять, когда")).toBeInTheDocument();
    expect(screen.getByLabelText("Текст навыка вручную")).toBeInTheDocument();
  });
});
