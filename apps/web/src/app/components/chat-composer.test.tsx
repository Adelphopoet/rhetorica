import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentProfile } from "@/features/agents/agentsApi";
import { useAgentMenuStore } from "@/features/agents/agentMenuStore";
import { ChatComposer } from "./chat-composer";

function makeAgent(index: number): AgentProfile {
  return {
    id: `agent-${index}`,
    workspace_id: "default",
    slug: `agent-${index}`,
    title: `Agent ${index}`,
    description: `Agent ${index} description`,
    is_active: true,
    llm_connector_name: null,
    allowed_database_connector_names: [],
    allowed_rag_connector_names: [],
    allowed_documentation_connector_names: [],
    allowed_gitlab_connector_names: [],
    allowed_mcp_connector_names: [],
    allowed_airflow_connector_names: [],
    allowed_kubernetes_connector_names: [],
    allowed_victoriametrics_connector_names: [],
    allowed_workspace_skill_ids: [],
    settings: {
      run_sql_allowed: true,
      temperature: null,
      max_tokens: null,
    },
    star_count: 0,
    is_starred: false,
    is_favorite: false,
    created_at: "2026-07-23T00:00:00Z",
    updated_at: "2026-07-23T00:00:00Z",
  };
}

describe("ChatComposer", () => {
  beforeEach(() => {
    useAgentMenuStore.setState({ agentMenuOpen: false, agentMenuPulse: false });
  });

  it("caps a bottom composer when the viewport height shrinks", () => {
    render(
      <ChatComposer
        value=""
        loading={false}
        chatWidth={1100}
        dock="bottom"
        onToggleDock={vi.fn()}
        dockWidth={380}
        dockHeight={500}
        maxDockHeight={240}
        onResizeDockWidth={vi.fn()}
        onResizeDockHeight={vi.fn()}
        onChange={() => undefined}
        onSubmit={() => undefined}
        onCancel={() => undefined}
      />
    );

    expect(screen.getByTestId("chat-composer")).toHaveStyle({
      height: "500px",
      maxHeight: "240px",
    });
  });

  it("submits on Enter without Shift", () => {
    const onSubmit = vi.fn();

    render(
      <ChatComposer
        value="show weekly GMV"
        loading={false}
        chatWidth={1100}
        dock="bottom"
        onToggleDock={vi.fn()}
        dockWidth={380}
        dockHeight={170}
        onResizeDockWidth={vi.fn()}
        onResizeDockHeight={vi.fn()}
        onChange={() => undefined}
        onSubmit={onSubmit}
        onCancel={() => undefined}
      />
    );

    fireEvent.keyDown(screen.getByPlaceholderText(/Задайте вопрос о метрике/i), {
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
        chatWidth={1100}
        dock="bottom"
        onToggleDock={vi.fn()}
        dockWidth={380}
        dockHeight={170}
        onResizeDockWidth={vi.fn()}
        onResizeDockHeight={vi.fn()}
        onChange={() => undefined}
        onSubmit={() => undefined}
        onCancel={onCancel}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /отмена/i }));

    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("keeps every agent selectable inside a scrollable menu", () => {
    const onSelectAgent = vi.fn();
    const agents = Array.from({ length: 25 }, (_, index) => makeAgent(index + 1));

    render(
      <ChatComposer
        value=""
        loading={false}
        chatWidth={1100}
        dock="bottom"
        onToggleDock={vi.fn()}
        dockWidth={380}
        dockHeight={170}
        onResizeDockWidth={vi.fn()}
        onResizeDockHeight={vi.fn()}
        agents={agents}
        onChange={() => undefined}
        onSelectAgent={onSelectAgent}
        onSubmit={() => undefined}
        onCancel={() => undefined}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Агент" }));

    expect(screen.getByRole("menu")).toHaveClass("overflow-y-auto", "overscroll-contain");
    expect(screen.getByRole("menu")).not.toHaveClass("glow-pulse");
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Agent 25/i }));
    expect(onSelectAgent).toHaveBeenCalledWith("agent-25");
  });

  it("shows a wait indicator on star only while the star mutation is pending", () => {
    const { rerender } = render(
      <ChatComposer
        value=""
        loading={false}
        chatWidth={1100}
        dock="bottom"
        onToggleDock={vi.fn()}
        dockWidth={380}
        dockHeight={170}
        maxDockHeight={900}
        maxDockWidth={1400}
        onResizeDockWidth={vi.fn()}
        onResizeDockHeight={vi.fn()}
        agents={[makeAgent(1)]}
        selectedAgentId="agent-1"
        agentStarPendingAgentId="agent-1"
        onChange={() => undefined}
        onSubmit={() => undefined}
        onCancel={() => undefined}
      />
    );

    const star = screen.getByRole("button", { name: "Отметить агента" });
    const favorite = screen.getByRole("button", { name: "Добавить агента в избранное" });

    expect(star).toHaveAttribute("aria-busy", "true");
    expect(star).toBeDisabled();
    expect(favorite).not.toHaveAttribute("aria-busy");
    expect(favorite).toBeEnabled();

    rerender(
      <ChatComposer
        value=""
        loading={false}
        chatWidth={1100}
        dock="bottom"
        onToggleDock={vi.fn()}
        dockWidth={380}
        dockHeight={170}
        maxDockHeight={900}
        maxDockWidth={1400}
        onResizeDockWidth={vi.fn()}
        onResizeDockHeight={vi.fn()}
        agents={[makeAgent(1)]}
        selectedAgentId="agent-1"
        agentFavoritePendingAgentId="agent-1"
        onChange={() => undefined}
        onSubmit={() => undefined}
        onCancel={() => undefined}
      />
    );

    expect(screen.getByRole("button", { name: "Добавить агента в избранное" })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Добавить агента в избранное" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Отметить агента" })).not.toHaveAttribute("aria-busy");
    expect(screen.getByRole("button", { name: "Отметить агента" })).toBeEnabled();
  });

  it("shows the dock toggle and reports the layout switch", () => {
    const onToggleDock = vi.fn();

    const { rerender } = render(
      <ChatComposer
        value=""
        loading={false}
        chatWidth={1100}
        dock="bottom"
        onToggleDock={onToggleDock}
        dockWidth={380}
        dockHeight={170}
        onResizeDockWidth={vi.fn()}
        onResizeDockHeight={vi.fn()}
        onChange={() => undefined}
        onSubmit={() => undefined}
        onCancel={() => undefined}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /Разместить справа/i }));
    expect(onToggleDock).toHaveBeenCalledTimes(1);

    rerender(
      <ChatComposer
        value=""
        loading={false}
        chatWidth={1100}
        dock="right"
        onToggleDock={onToggleDock}
        dockWidth={380}
        dockHeight={170}
        onResizeDockWidth={vi.fn()}
        onResizeDockHeight={vi.fn()}
        onChange={() => undefined}
        onSubmit={() => undefined}
        onCancel={() => undefined}
      />
    );

    expect(screen.getByRole("button", { name: /Разместить внизу/i })).toBeInTheDocument();
  });
});

it("places coding after the agent selector and filters agents in the menu", () => {
  useAgentMenuStore.setState({ agentMenuOpen: false, agentMenuPulse: false });
  const select = vi.fn();
  render(<ChatComposer value="" loading={false} chatWidth={1100} dock="bottom" onToggleDock={vi.fn()} dockWidth={380} dockHeight={300} onResizeDockWidth={vi.fn()} onResizeDockHeight={vi.fn()} agents={[makeAgent(1), makeAgent(2)]} onSelectAgent={select} codingControls={<button>Coding test</button>} onChange={vi.fn()} onSubmit={vi.fn()} onCancel={vi.fn()} />);
  const agent = screen.getByRole("button", { name: "Агент" });
  expect(agent.compareDocumentPosition(screen.getByRole("button", { name: "Coding test" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(agent);
  const search = screen.getByRole("searchbox", { name: "Поиск агента" });
  fireEvent.change(search, { target: { value: "Agent 2" } });
  expect(screen.queryByRole("menuitemradio", { name: /Agent 1/ })).not.toBeInTheDocument();
  const target = screen.getByRole("menuitemradio", { name: /Agent 2/ });
  fireEvent.keyDown(search, { key: "ArrowDown" });
  expect(target).toHaveFocus();
  fireEvent.click(target);
  expect(select).toHaveBeenCalledWith("agent-2");
});

it("offers coding teams beside agents and labels the selected team", () => {
  const onSelectTeam = vi.fn();
  const team = { id: "team-1", name: "dbt team", available: true, coordinator_agent_profile_id: "agent-1", members: [{ id: "engineer", label: "Data engineer", capability: "candidate" as const }, { id: "reviewer", label: "Reviewer", capability: "review" as const }] };
  const props = { value: "", loading: false, chatWidth: 1100 as const, dock: "bottom" as const, onToggleDock: vi.fn(), dockWidth: 380, dockHeight: 500, onResizeDockWidth: vi.fn(), onResizeDockHeight: vi.fn(), onChange: () => undefined, onSubmit: () => undefined, onCancel: () => undefined, agents: [makeAgent(1)], teams: [team], onSelectTeam };
  const { rerender } = render(<ChatComposer {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Агент" }));

  const row = screen.getByRole("menuitemradio", { name: /dbt team/ });
  expect(row).toHaveTextContent("Координатор: Agent 1 · Data engineer, Reviewer");
  fireEvent.click(row);
  expect(onSelectTeam).toHaveBeenCalledWith(team);

  rerender(<ChatComposer {...props} selectedAgentId="agent-1" selectedTeamId="team-1" />);
  expect(screen.getByRole("button", { name: "Агент" })).toHaveTextContent("dbt team · команда");
});
