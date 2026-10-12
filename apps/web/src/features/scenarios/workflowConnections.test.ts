import { MarkerType } from "@xyflow/react";
import { describe, expect, it } from "vitest";
import { newNode, type WorkflowDefinition } from "./workflowApi";
import { connectWorkflow, deleteWorkflowElements, updateNodeBindings, workflowEdges } from "./workflowConnections";
import { inputHandle, outputHandle } from "./workflowPorts";
import { templateGraph } from "./workflowAuthoring";

function disconnected(): WorkflowDefinition {
  return { schema_version: 1, parameters: { test_param: { type: "string" } }, nodes: [newNode("input", "parameters"), newNode("python", "python")], edges: [], allowed_tools: [] };
}
const parameter = { source: "input", path: ["test_param"], type: "string" } as const;
const inputBinding = () => ({ ...parameter, path: [...parameter.path] });

describe("workflow input connections", () => {
  it("renders a previously saved parameter binding even when the graph has no execution edge", () => {
    const graph = disconnected();
    graph.nodes[1].inputs = { test_param: inputBinding() };
    expect(workflowEdges(graph)).toEqual([expect.objectContaining({ source: "parameters", target: "python", label: "test_param: Текст", data: { inputName: "test_param" }, markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(var(--color-purple))" } })]);
    expect(graph.edges).toEqual([]);
  });
  it("wires a disconnected Python step after choosing a parameter and merges the arrow with execution order", () => {
    const graph = disconnected();
    const updated = updateNodeBindings(graph, { ...graph.nodes[1], inputs: { test_param: inputBinding() } });
    expect(updated.edges).toEqual([{ source: "parameters", target: "python" }]);
    expect(workflowEdges(updated)).toHaveLength(1);
    expect(workflowEdges(updated)[0]).toMatchObject({ id: "e0", label: "test_param: Текст", markerEnd: expect.objectContaining({ type: MarkerType.ArrowClosed }) });
    expect(updateNodeBindings(updated, { ...updated.nodes[1], title: "Updated" }).edges).toEqual(updated.edges);
    expect(graph.edges).toEqual([]);
  });
  it("does not add an unconditional shortcut around a branch or approval", () => {
    const graph = disconnected();
    graph.nodes.push(newNode("condition", "condition"), newNode("approval", "approval"));
    graph.edges = [{ source: "parameters", target: "condition" }, { source: "condition", target: "approval", branch: "true" }, { source: "approval", target: "python" }];
    const updated = updateNodeBindings(graph, { ...graph.nodes[1], inputs: { test_param: inputBinding() } });
    expect(updated.edges).toEqual(graph.edges);
    expect(workflowEdges(updated)).toContainEqual(expect.objectContaining({ source: "parameters", target: "python", style: expect.objectContaining({ strokeDasharray: "6 4" }) }));
  });
  it("shows transitive file references and updates their labels without changing control edges", () => {
    const { workflow } = templateGraph("analysis", "db", "agent");
    const before = structuredClone(workflow.edges);
    expect(workflowEdges(workflow)).toContainEqual(expect.objectContaining({ source: "python", target: "result", label: "files: Файлы" }));
    workflow.nodes.find(node => node.id === "result")!.inputs = { output_files: { source: "step", node_id: "python", path: ["files"], type: "files" } };
    expect(workflowEdges(workflow)).toContainEqual(expect.objectContaining({ source: "python", target: "result", label: "files → output_files: Файлы" }));
    workflow.nodes.find(node => node.id === "result")!.inputs = {};
    expect(workflowEdges(workflow).some(edge => edge.source === "python" && edge.target === "result")).toBe(false);
    expect(workflow.edges).toEqual(before);
  });
  it("does not draw constants as if they came from the parameters node", () => {
    const graph = disconnected();
    const updated = updateNodeBindings(graph, { ...graph.nodes[1], inputs: { value: { source: "constant", type: "string", value: "test" } } });
    expect(updated.edges).toEqual([]);
    expect(workflowEdges(updated)).toEqual([]);
  });
});

describe("dragging typed ports", () => {
  const setup = () => ({ ...disconnected(), nodes: [...disconnected().nodes, newNode("llm", "llm")] });
  const wire = (path = ["result"]) => ({ source: "python", target: "llm", sourceHandle: outputHandle(path), targetHandle: "add-input" });

  it("connects disconnected Python and LLM steps with a typed binding and execution dependency", () => {
    const initial = setup();
    const graph = connectWorkflow(initial, wire());
    expect(graph.nodes[2].inputs.result).toMatchObject({ source: "step", node_id: "python", type: "json", path: ["result"] });
    expect(graph.edges).toEqual([{ source: "python", target: "llm" }]);
    expect(workflowEdges(graph)[0]).toMatchObject({ sourceHandle: outputHandle(["result"]), targetHandle: inputHandle("result") });
    expect(connectWorkflow(graph, wire())).toBe(graph);
    expect(initial.nodes[2].inputs).toEqual({});
  });

  it("wires individual JSON fields and optional files to distinct handles", () => {
    const initial = setup();
    initial.nodes[1].outputs.result.json_schema = { type: "object", properties: { summary: { type: "string" } }, required: ["summary"] };
    const graph = connectWorkflow(connectWorkflow(initial, wire(["result", "summary"])), wire(["files"]));
    expect(graph.nodes[2].inputs.summary).toMatchObject({ type: "string", path: ["result", "summary"], required: true });
    expect(graph.nodes[2].inputs.files).toMatchObject({ type: "files", path: ["files"], required: false });
    expect(graph.edges).toHaveLength(1);
    expect(workflowEdges(graph)).toEqual([
      expect.objectContaining({ sourceHandle: outputHandle(["result", "summary"]), targetHandle: inputHandle("summary") }),
      expect.objectContaining({ sourceHandle: outputHandle(["files"]), targetHandle: inputHandle("files") }),
    ]);
  });

  it("rejects incompatible types and cycles without changing the graph", () => {
    const initial = setup();
    initial.nodes[2].inputs.prompt = { source: "constant", type: "string", value: "test" };
    expect(() => connectWorkflow(initial, { ...wire(), targetHandle: inputHandle("prompt") })).toThrow("Несовместимые типы");
    const graph = connectWorkflow(initial, wire());
    expect(() => connectWorkflow(graph, { source: "llm", target: "python", sourceHandle: outputHandle(["result"]), targetHandle: "add-input" })).toThrow("цикл");
    expect(initial.edges).toEqual([]);
    expect(initial.nodes[2].inputs.prompt.value).toBe("test");
  });

  it("does not create an unconditional path into a gated step", () => {
    const graph = setup();
    graph.nodes.push(newNode("approval", "approval"));
    graph.edges = [{ source: "parameters", target: "approval" }, { source: "approval", target: "llm" }];
    expect(() => connectWorkflow(graph, wire())).toThrow("подтверждений");
    expect(graph.edges).toHaveLength(2);
  });

  it("deletes data bindings with their wires while preserving dependencies of other inputs", () => {
    const initial = connectWorkflow(connectWorkflow(setup(), wire()), wire(["files"]));
    const graph = deleteWorkflowElements(initial, { nodes: [], edges: [workflowEdges(initial)[0]] });
    expect(graph.nodes[2].inputs.result).toBeUndefined();
    expect(graph.nodes[2].inputs.files).toMatchObject({ node_id: "python", type: "files" });
    expect(graph.edges).toEqual(initial.edges);
    const empty = deleteWorkflowElements(graph, { nodes: [], edges: workflowEdges(graph) });
    expect(empty.nodes[2].inputs).toEqual({});
    expect(workflowEdges(empty)).toEqual([]);
    expect(initial.nodes[2].inputs.result).toBeDefined();
  });

  it("removes references to a deleted source node", () => {
    const graph = deleteWorkflowElements(connectWorkflow(setup(), wire()), { nodes: [{ id: "python" }], edges: [] });
    expect(graph.nodes.find(node => node.id === "llm")!.inputs).toEqual({});
    expect(workflowEdges(graph)).toEqual([]);
  });
});
