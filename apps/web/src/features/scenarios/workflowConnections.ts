import { MarkerType, type Connection, type Edge } from "@xyflow/react";
import { ancestors, connectData, valueLabels } from "./workflowAuthoring";
import { dataConnectionIssue, defaultOutput, inputHandle, outputHandle, outputPorts, passthroughInputs, portBinding, preferredInputName } from "./workflowPorts";
import type { Binding, WorkflowDefinition, WorkflowNode } from "./workflowApi";

type DataConnection = { source: string; target: string; name: string; path: (string | number)[]; label: string };

function bindingSource(graph: WorkflowDefinition, binding: Binding): string | undefined {
  if (binding.source === "input") return graph.nodes.find(node => node.type === "input")?.id;
  if (binding.source === "step") return binding.node_id ?? undefined;
}

export function workflowEdges(graph: WorkflowDefinition): Edge[] {
  const connections: DataConnection[] = [];
  for (const node of graph.nodes) {
    for (const [name, binding] of Object.entries(node.inputs)) {
      const source = bindingSource(graph, binding);
      if (!source || source === node.id || !graph.nodes.some(node => node.id === source)) continue;
      const path = binding.path?.join(".") || "результат";
      connections.push({ source, target: node.id, name, path: binding.path ?? [], label: `${path === name ? name : `${path} → ${name}`}: ${valueLabels[binding.type]}` });
    }
  }
  const sourceHandle = (connection: DataConnection) => {
    const node = graph.nodes.find(node => node.id === connection.source)!;
    const port = outputPorts(node, graph.parameters).filter(port => port.path.every((part, index) => part === connection.path[index])).sort((a, b) => b.path.length - a.path.length)[0];
    return port ? outputHandle(port.path) : undefined;
  };
  const rendered = new Set<DataConnection>();
  const edges: Edge[] = graph.edges.map((edge, index) => {
    const connection = !edge.branch ? connections.find(item => item.source === edge.source && item.target === edge.target && !rendered.has(item)) : undefined;
    if (connection) rendered.add(connection);
    const color = edge.branch === "false" ? "rgb(var(--color-orange))" : "rgb(var(--color-cyan))";
    return { id: `e${index}`, source: edge.source, target: edge.target,
      sourceHandle: edge.branch ?? (connection ? sourceHandle(connection) : undefined), targetHandle: connection ? inputHandle(connection.name) : undefined,
      label: edge.branch ?? connection?.label ?? "",
      data: connection ? { inputName: connection.name } : undefined,
      ariaLabel: `Порядок выполнения: ${edge.source} → ${edge.target}${connection ? `. Данные: ${connection.label}` : ""}`,
      markerEnd: { type: MarkerType.ArrowClosed, color },
      style: { stroke: color, strokeWidth: 1.8 },
      labelStyle: { fill: "rgb(var(--color-secondary))", fontSize: 10 }, labelBgStyle: { fill: "rgb(var(--color-surface))" } };
  });
  for (const connection of connections) {
    if (rendered.has(connection)) continue;
    // Data references do not introduce unconditional control paths around gates.
    edges.push({ id: `data:${JSON.stringify([connection.source, connection.target, connection.name])}`, source: connection.source, target: connection.target,
      sourceHandle: sourceHandle(connection), targetHandle: inputHandle(connection.name),
      reconnectable: false, data: { inputName: connection.name },
      label: connection.label, ariaLabel: `Данные: ${connection.source} → ${connection.target}. ${connection.label}`,
      markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(var(--color-purple))" },
      style: { stroke: "rgb(var(--color-purple))", strokeWidth: 1.6, strokeDasharray: "6 4" },
      labelStyle: { fill: "rgb(var(--color-purple))", fontSize: 10 }, labelBgStyle: { fill: "rgb(var(--color-surface))" } });
  }
  return edges;
}

export function updateNodeBindings(graph: WorkflowDefinition, node: WorkflowNode): WorkflowDefinition {
  const previous = graph.nodes.find(item => item.id === node.id);
  const next = { ...graph, nodes: graph.nodes.map(item => item.id === node.id ? node : item) };
  // New sources become execution dependencies unless already guaranteed by the
  // control graph. In particular, do not add shortcuts around conditions/gates.
  if (node.type === "input") return next;
  const sources = new Set(Object.entries(node.inputs).flatMap(([key, binding]) => {
    const source = bindingSource(graph, binding);
    if (!source || source === bindingSource(graph, previous?.inputs[key] ?? { source: "constant", type: "json" })) return [];
    return [source];
  }));
  const edges = [...graph.edges];
  for (const source of sources) {
    const parent = graph.nodes.find(item => item.id === source);
    if (!parent || dataConnectionIssue(graph, source, node.id) || ancestors(graph, node.id).some(item => item.id === source)) continue;
    if (parent.type === "input" && graph.edges.some(edge => edge.target === node.id)) continue;
    edges.push({ source, target: node.id });
  }
  return { ...next, edges };
}

export function deleteWorkflowElements(graph: WorkflowDefinition, deleted: { nodes: { id: string }[]; edges: Edge[] }): WorkflowDefinition {
  const removedNodes = new Set(deleted.nodes.map(node => node.id));
  const removedEdges = new Set(deleted.edges.map(edge => edge.id));
  const nodes = graph.nodes.filter(node => !removedNodes.has(node.id)).map(node => {
    const inputs = Object.fromEntries(Object.entries(node.inputs).filter(([name, binding]) => {
      const source = bindingSource(graph, binding);
      return !(source && removedNodes.has(source)) && !deleted.edges.some(edge => edge.target === node.id && edge.data?.inputName === name);
    }));
    return Object.keys(inputs).length === Object.keys(node.inputs).length ? node : passthroughInputs(node, inputs);
  });
  const edges = graph.edges.filter((edge, index) => {
    if (removedNodes.has(edge.source) || removedNodes.has(edge.target)) return false;
    if (!removedEdges.has(`e${index}`)) return true;
    // Multiple data wires can share one execution dependency. Keep that
    // dependency until its last directly connected input is removed.
    if (!deleted.edges.some(item => item.id === `e${index}` && item.data?.inputName)) return false;
    return Object.values(nodes.find(node => node.id === edge.target)?.inputs ?? {}).some(binding => bindingSource(graph, binding) === edge.source);
  });
  return { ...graph, nodes, edges };
}

export function connectWorkflow(graph: WorkflowDefinition, connection: Connection): WorkflowDefinition {
  const source = graph.nodes.find(node => node.id === connection.source);
  const target = graph.nodes.find(node => node.id === connection.target);
  if (!source || !target) throw new Error("Шаг не найден");
  const dataPort = connection.sourceHandle?.startsWith("out:");
  const inputPort = connection.targetHandle?.startsWith("in:");
  if (dataPort || inputPort || connection.targetHandle === "add-input") {
    const issue = dataConnectionIssue(graph, source.id, target.id);
    if (issue) throw new Error(issue);
    const port = dataPort ? outputPorts(source, graph.parameters).find(port => outputHandle(port.path) === connection.sourceHandle) : defaultOutput(source, target, graph.parameters);
    if (!port) throw new Error("Выберите выход результата на исходном шаге");
    const key = inputPort ? Object.keys(target.inputs).find(name => inputHandle(name) === connection.targetHandle) : undefined;
    if (inputPort && !key) throw new Error("Вход шага больше не существует");
    if (key && target.inputs[key].type !== port.type) throw new Error(`Несовместимые типы: ${valueLabels[port.type]} → ${valueLabels[target.inputs[key].type]}. Используйте «+ Вход» для нового значения`);
    const binding = portBinding(source, port);
    // Repeated drops of the same output on the add-input port are idempotent.
    if (!key && Object.values(target.inputs).some(input => input.source === binding.source && input.node_id === binding.node_id && JSON.stringify(input.path) === JSON.stringify(binding.path))) return graph;
    let name = key ?? preferredInputName(port); let count = 2;
    while (!key && name in target.inputs) name = `${preferredInputName(port)}_${count++}`;
    return updateNodeBindings(graph, passthroughInputs(target, { ...target.inputs, [name]: binding }));
  }
  if (source.id === target.id || target.type === "input" || source.type === "result" || ancestors(graph, source.id).some(node => node.id === target.id)) throw new Error("Эта связь создаст цикл или нарушит порядок шагов");
  const branch = source.type === "condition" && ["true", "false"].includes(connection.sourceHandle ?? "") ? connection.sourceHandle as "true" | "false" : null;
  if (source.type === "condition" && !branch) throw new Error("Выберите ветвь «Да» или «Нет»");
  if (graph.edges.some(edge => edge.source === source.id && edge.target === target.id && (edge.branch ?? null) === branch)) return graph;
  return { ...graph, nodes: graph.nodes.map(node => node.id === target.id && !branch ? connectData(source, node) : node), edges: [...graph.edges, { source: source.id, target: target.id, branch }] };
}
