import { useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ReactFlowProvider, type ReactFlowProps } from "@xyflow/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ScenarioGraphNode, WorkflowCanvas } from "./WorkflowCanvas";
import { Bindings } from "./WorkflowNodeSettings";
import { newNode, type WorkflowDefinition } from "./workflowApi";
import { connectWorkflow, updateNodeBindings } from "./workflowConnections";
import { inputHandle, outputHandle } from "./workflowPorts";
import { templateGraph } from "./workflowAuthoring";

vi.mock("./monaco", () => ({}));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));

const currentTheme = vi.hoisted(() => ({ resolvedTheme: "dark" as "dark" | "light" }));
vi.mock("@/features/theme/ThemeProvider", () => ({ useTheme: () => currentTheme }));

const captured = vi.hoisted(() => ({ current: {} as ReactFlowProps }));
vi.mock("@xyflow/react", async original => ({ ...await original<typeof import("@xyflow/react")>(), ReactFlow: (props: ReactFlowProps) => { captured.current = props; return <div />; }, Controls: () => null, MiniMap: () => null, Background: () => null }));

describe("workflow canvas", () => {
  beforeEach(() => { currentTheme.resolvedTheme = "dark"; });
  it("renders labeled result and file handles on Python nodes", () => {
    const node = newNode("python", "python");
    node.outputs.result.json_schema = { type: "object", properties: { summary: { type: "string" } } };
    render(<ReactFlowProvider><ScenarioGraphNode id="python" type="scenario" data={{ step: node, invalid: false }} selected={false} selectable draggable deletable isConnectable dragging={false} zIndex={0} positionAbsoluteX={0} positionAbsoluteY={0} /></ReactFlowProvider>);
    for (const label of ["Выход Python: result", "Выход Python: result.summary", "Выход Python: files", "Добавить вход Python"]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    expect(screen.getByLabelText("Выход Python: result.summary")).toHaveAttribute("data-handleid", outputHandle(["result", "summary"]));
  });

  it("keeps measurements and moving positions local, committing a drag once for undo", () => {
    const graph = templateGraph("analysis", "db", "agent");
    const positions = vi.fn(); const viewport = vi.fn();
    const props = { graph: graph.workflow, layout: graph.layout, selected: "sql", validation: null, onPositions: positions, onViewport: viewport, onSelect: vi.fn(), onConnect: vi.fn(), onDelete: vi.fn() };
    const view = render(<WorkflowCanvas {...props} />);
    act(() => captured.current.onNodesChange?.([{ id: "sql", type: "dimensions", dimensions: { width: 224, height: 130 } }, { id: "sql", type: "position", position: { x: 400, y: 200 }, dragging: true }]));
    expect(positions).not.toHaveBeenCalled();
    const sql = captured.current.nodes!.find(node => node.id === "sql")!;
    expect(sql.position).toEqual({ x: 400, y: 200 });
    expect(sql.measured).toEqual({ width: 224, height: 130 });
    view.rerender(<WorkflowCanvas {...props} selected="python" />);
    expect(captured.current.nodes!.find(node => node.id === "sql")!.position).toEqual({ x: 400, y: 200 });
    act(() => captured.current.onNodeDragStop?.({} as MouseEvent, sql, [sql]));
    expect(positions).toHaveBeenCalledExactlyOnceWith({ sql: { x: 400, y: 200 } });
    act(() => captured.current.onMoveEnd?.({} as MouseEvent, { x: 80, y: 50, zoom: 0.8 }));
    expect(positions).toHaveBeenCalledTimes(1);
    expect(viewport).toHaveBeenCalledWith({ x: 80, y: 50, zoom: 0.8 });
    expect(captured.current.onlyRenderVisibleElements).toBe(false);
    expect(captured.current.connectionRadius).toBe(32);
  });
  it("allows selecting edges for deletion", () => {
    const graph = templateGraph("sql", "db", "agent");
    render(<WorkflowCanvas graph={graph.workflow} layout={graph.layout} selected="sql" validation={null} onPositions={vi.fn()} onViewport={vi.fn()} onSelect={vi.fn()} onConnect={vi.fn()} onDelete={vi.fn()} />);
    act(() => captured.current.onEdgesChange?.([{ id: "e0", type: "select", selected: true }]));
    expect(captured.current.edges![0].selected).toBe(true);
  });
  it("draws the arrow immediately when a Python input selects a scenario parameter, including undo", () => {
    const initial: WorkflowDefinition = { schema_version: 1, parameters: { test_param: { type: "string" } }, nodes: [newNode("input", "parameters"), newNode("python", "python")], edges: [], allowed_tools: [] };
    function Editor() {
      const [graph, setGraph] = useState(initial);
      return <><WorkflowCanvas graph={graph} layout={{ positions: {} }} selected="python" validation={null} onPositions={vi.fn()} onViewport={vi.fn()} onSelect={vi.fn()} onConnect={vi.fn()} onDelete={vi.fn()} /><Bindings node={graph.nodes[1]} graph={graph} update={node => setGraph(current => updateNodeBindings(current, node))} /><button onClick={() => setGraph(initial)}>Undo</button></>;
    }
    render(<Editor />);
    expect(captured.current.edges).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Добавить данные предыдущего шага"), { target: { value: "input:test_param" } });
    expect(captured.current.edges).toHaveLength(1);
    expect(captured.current.edges![0]).toMatchObject({ source: "parameters", target: "python", label: "test_param: Текст" });
    fireEvent.click(screen.getByText("Undo"));
    expect(captured.current.edges).toHaveLength(0);
  });

  it("updates the LLM inputs and handle endpoints when the canvas receives a mouse connection", () => {
    const initial: WorkflowDefinition = { schema_version: 1, parameters: {}, nodes: [newNode("python", "python"), newNode("llm", "llm")], edges: [], allowed_tools: [] };
    function Editor() {
      const [graph, setGraph] = useState(initial);
      return <><WorkflowCanvas graph={graph} layout={{ positions: {} }} selected="llm" validation={null} onPositions={vi.fn()} onViewport={vi.fn()} onSelect={vi.fn()} onConnect={connection => setGraph(current => connectWorkflow(current, connection))} onDelete={vi.fn()} /><Bindings node={graph.nodes[1]} graph={graph} update={node => setGraph(current => updateNodeBindings(current, node))} /></>;
    }
    render(<Editor />);
    const connection = { source: "python", target: "llm", sourceHandle: outputHandle(["result"]), targetHandle: "add-input" };
    expect(captured.current.isValidConnection?.(connection)).toBe(true);
    act(() => captured.current.onConnect?.(connection));
    expect(screen.getByLabelText("Источник result")).toHaveValue("python:result");
    expect(captured.current.edges![0]).toMatchObject({ source: "python", target: "llm", sourceHandle: outputHandle(["result"]), targetHandle: inputHandle("result") });
    expect(captured.current.isValidConnection?.({ ...connection, source: "llm", target: "python" })).toBe(false);
  });

  it("switches canvas theme while preserving drag measurements, edge selection and viewport", () => {
    const graph = templateGraph("sql", "db", "agent");
    const positions = vi.fn(); const viewport = vi.fn();
    const props = { graph: graph.workflow, layout: { ...graph.layout, viewport: { x: 50, y: 75, zoom: 0.8 } }, selected: "sql", validation: null, onPositions: positions, onViewport: viewport, onSelect: vi.fn(), onConnect: vi.fn(), onDelete: vi.fn() };
    const view = render(<WorkflowCanvas {...props} />);
    expect(captured.current.colorMode).toBe("dark");
    act(() => {
      captured.current.onNodesChange?.([{ id: "sql", type: "dimensions", dimensions: { width: 250, height: 130 } }, { id: "sql", type: "position", position: { x: 400, y: 200 }, dragging: true }]);
      captured.current.onEdgesChange?.([{ id: "e0", type: "select", selected: true }]);
    });
    currentTheme.resolvedTheme = "light";
    // React.memo consumers respond through context in production; this test forces
    // the rerender because the theme hook is mocked with a mutable snapshot.
    view.rerender(<WorkflowCanvas {...props} onSelect={vi.fn()} />);
    expect(captured.current.colorMode).toBe("light");
    expect(captured.current.nodes!.find(node => node.id === "sql")).toMatchObject({ position: { x: 400, y: 200 }, measured: { width: 250, height: 130 } });
    expect(captured.current.edges![0].selected).toBe(true);
    expect(captured.current.defaultViewport).toEqual({ x: 50, y: 75, zoom: 0.8 });
    expect(positions).not.toHaveBeenCalled();
    expect(viewport).not.toHaveBeenCalled();
  });

});
