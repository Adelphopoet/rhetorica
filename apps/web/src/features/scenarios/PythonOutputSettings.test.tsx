import { useEffect, useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PythonOutputSettings } from "./PythonOutputSettings";
import { Bindings } from "./WorkflowNodeSettings";
import { newNode, type WorkflowDefinition } from "./workflowApi";
import { updateNodeBindings } from "./workflowConnections";
import { outputHandle, outputPorts } from "./workflowPorts";

vi.mock("./monaco", () => ({}));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));

describe("Python output authoring", () => {
  it("exposes a configured field to a disconnected LLM and connects it without editing JSON", () => {
    let graph: WorkflowDefinition;
    function Editor() {
      const [current, setGraph] = useState<WorkflowDefinition>({ schema_version: 1, parameters: {}, nodes: [newNode("input", "input"), newNode("python", "python"), newNode("llm", "llm")], edges: [], allowed_tools: [] });
      useEffect(() => { graph = current; }, [current]);
      return <><PythonOutputSettings node={current.nodes[1]} update={node => setGraph(graph => updateNodeBindings(graph, node))} /><Bindings node={current.nodes[2]} graph={current} update={node => setGraph(graph => updateNodeBindings(graph, node))} /></>;
    }
    render(<Editor />);
    expect(screen.getByLabelText("Добавить данные предыдущего шага")).toHaveTextContent("Python → result · JSON");
    const originalCode = graph!.nodes[1].code;
    fireEvent.change(screen.getByLabelText("Имя поля результата Python"), { target: { value: "summary" } });
    fireEvent.click(screen.getByText("Добавить поле"));
    expect(graph!.nodes[1].code).toBe(originalCode);
    expect(outputPorts(graph!.nodes[1])).toContainEqual({ path: ["result", "summary"], label: "result.summary", type: "string", required: true });
    fireEvent.change(screen.getByLabelText("Добавить данные предыдущего шага"), { target: { value: `python:${outputHandle(["result", "summary"])}` } });
    expect(graph!.nodes[2].inputs.summary).toMatchObject({ node_id: "python", type: "string", path: ["result", "summary"] });
    expect(graph!.edges).toEqual([{ source: "python", target: "llm" }]);
    fireEvent.click(screen.getByLabelText("Обязательное поле"));
    expect(outputPorts(graph!.nodes[1]).find(port => port.label === "result.summary")?.required).toBe(false);
    fireEvent.click(screen.getByText("Вставить пример в код"));
    expect(graph!.nodes[1].code).toContain('"summary": "..."');
  });

  it("changes the fixed result port to text while retaining file outputs", () => {
    const update = vi.fn();
    const node = newNode("python", "python");
    node.outputs.result.json_schema = { type: "object", properties: { count: { type: "number" } } };
    render(<PythonOutputSettings node={node} update={update} />);
    fireEvent.change(screen.getByLabelText("Тип результата Python"), { target: { value: "string" } });
    expect(update).toHaveBeenCalledWith(expect.objectContaining({ outputs: { result: expect.objectContaining({ type: "string", json_schema: null }), files: node.outputs.files } }));
  });
});
