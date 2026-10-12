import { ancestors } from "./workflowAuthoring";
import type { Binding, ValueSpec, ValueType, WorkflowDefinition, WorkflowNode } from "./workflowApi";

export type OutputPort = { path: string[]; label: string; type: ValueType; required: boolean };
export const outputHandle = (path: (string | number)[]) => `out:${encodeURIComponent(JSON.stringify(path))}`;
export const inputHandle = (name: string) => `in:${encodeURIComponent(name)}`;

export function outputPorts(node: WorkflowNode, parameters: Record<string, ValueSpec> = {}): OutputPort[] {
  return Object.entries(node.type === "input" ? parameters : node.outputs).flatMap(([name, spec]) => {
    const ports: OutputPort[] = [{ path: [name], label: name, type: spec.type, required: spec.required !== false }];
    const properties = spec.json_schema?.properties;
    if (spec.type === "json" && properties && typeof properties === "object" && !Array.isArray(properties)) {
      const required = spec.json_schema?.required;
      for (const [key, raw] of Object.entries(properties)) {
        const schema = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
        const type: ValueType = schema.type === "string" ? "string" : ["number", "integer"].includes(String(schema.type)) ? "number" : schema.type === "boolean" ? "boolean" : "json";
        ports.push({ path: [name, key], label: `${name}.${key}`, type, required: spec.required !== false && Array.isArray(required) && required.includes(key) });
      }
    }
    return ports;
  });
}

export function portBinding(node: WorkflowNode, port: OutputPort): Binding {
  return { source: node.type === "input" ? "input" : "step", ...(node.type === "input" ? {} : { node_id: node.id }), path: port.path, type: port.type, required: port.required };
}

export function dataConnectionIssue(graph: WorkflowDefinition, sourceId: string, targetId: string): string | undefined {
  const source = graph.nodes.find(node => node.id === sourceId);
  const target = graph.nodes.find(node => node.id === targetId);
  if (!source || !target || sourceId === targetId || target.type === "input" || source.type === "result") return "Выберите выход и вход разных шагов";
  if (ancestors(graph, sourceId).some(node => node.id === targetId)) return "Эта связь создаст цикл";
  const previous = ancestors(graph, targetId);
  if (previous.some(node => node.id === sourceId)) return;
  // Input values are available to every step without changing its activation.
  if (source.type === "input" && graph.edges.some(edge => edge.target === targetId)) return;
  if (source.type === "condition" || previous.some(node => ["condition", "approval"].includes(node.type))) return "Сначала задайте порядок ветвей и подтверждений стрелками выполнения";
}

export function availableOutputSources(graph: WorkflowDefinition, targetId: string): WorkflowNode[] {
  return graph.nodes.filter(node => node.type !== "input" && !dataConnectionIssue(graph, node.id, targetId));
}

export function defaultOutput(node: WorkflowNode, target: WorkflowNode, parameters: Record<string, ValueSpec>): OutputPort | undefined {
  const ports = outputPorts(node, parameters);
  const name = target.type === "python" && node.outputs.file ? "file" : node.outputs.result ? "result" : node.outputs.table ? "table" : undefined;
  return ports.find(port => port.path.length === 1 && port.path[0] === name) ?? ports[0];
}

export function passthroughInputs(node: WorkflowNode, inputs: Record<string, Binding>): WorkflowNode {
  return { ...node, inputs, ...(["join", "parallel", "approval", "result"].includes(node.type) ? { outputs: Object.fromEntries(Object.entries(inputs).map(([name, binding]) => [name, { type: binding.type, required: binding.required }])) } : {}) };
}

export function preferredInputName(port: OutputPort): string {
  const name = port.path.at(-1) ?? "value";
  return /^[a-zA-Z][a-zA-Z0-9_]*$/.test(name) ? name : "value";
}
