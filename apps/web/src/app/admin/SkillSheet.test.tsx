import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SkillSheet } from "./SkillSheet";

describe("SkillSheet", () => {
  it("offers Trino as an engine scope", () => {
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

    expect(screen.getByRole("option", { name: "ClickHouse" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Trino" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Impala" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Any" })).toBeInTheDocument();
  });
});
