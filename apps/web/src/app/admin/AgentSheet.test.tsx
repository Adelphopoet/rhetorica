import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentSheet } from "./AgentSheet";
import type { WorkspaceSkillItem } from "./types";

function makeSkill(index: number): WorkspaceSkillItem {
  return {
    id: `skill-${index}`,
    workspace_id: "default",
    name: `chat-skill-${index}`,
    capability_family: "chat_instruction",
    engine_scope: "any",
    priority: index,
    source_kind: "manual_markdown",
    source_url: null,
    status: "active",
    validation_status: "valid",
    visible_to_default_assistant: false,
    title: `Chat skill ${index}`,
    summary: `Chat skill ${index} summary`,
    content_markdown: `# Chat skill ${index}`,
    content_bundle: { assets: [] },
    content_sha256: null,
    last_validated_at: "2026-07-23T00:00:00Z",
    created_at: "2026-07-23T00:00:00Z",
    updated_at: "2026-07-23T00:00:00Z",
  };
}

describe("AgentSheet", () => {
  it("keeps every chat skill selectable inside a scrollable list", () => {
    const skills = Array.from({ length: 25 }, (_, index) => makeSkill(index + 1));
    const { container } = render(
      <AgentSheet
        open
        onOpenChange={vi.fn()}
        editingAgent={null}
        workspaceId="default"
        agents={[]}
        databaseConnectors={[]}
        llmConnectors={[]}
        documentationConnectors={[]}
        gitlabConnectors={[]}
        mcpConnectors={[]}
        airflowConnectors={[]}
        kubernetesConnectors={[]}
        victoriametricsConnectors={[]}
        workspaceSkills={skills}
        onSubmit={vi.fn(async () => undefined)}
        saving={false}
        error={null}
      />
    );

    expect(screen.getByRole("group", { name: "Доступные навыки для чата" })).toHaveClass(
      "overflow-y-auto",
      "overscroll-contain"
    );

    fireEvent.click(screen.getByRole("button", { name: /Chat skill 25/i }));
    expect(
      container.querySelector('input[name="workspace_skill_ids"][value="skill-25"]')
    ).toBeInTheDocument();
  });
});
