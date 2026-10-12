import { newNode, type Binding, type Capabilities, type Draft, type NodeType, type WorkflowDefinition, type WorkflowNode } from "./workflowApi";

export const valueLabels = { string: "Текст", number: "Число", boolean: "Да / нет", json: "JSON", table: "Таблица", file: "Файл", files: "Файлы" };
export const templates = {
  analysis: { title: "Анализ данных", description: "SQL → Python → Агент → Результат. Файл запроса уже подключён к Python" },
  sql: { title: "SQL и выгрузка", description: "Параметры → SQL → Результат. Таблица и полный файл запроса" },
  dbt: { title: "dbt: developer → QA → Draft MR", description: "Разработка в фоне с проверяемым diff и read-only QA. Выберите существующие ресурсы и специалистов в настройках шага" },
  agent: { title: "Ответ агента", description: "Вопрос → Агент → Результат. Простой старт без кода" },
};
export type Template = keyof typeof templates;

export function ancestors(graph: WorkflowDefinition, nodeId: string): WorkflowNode[] {
  const ids = new Set<string>();
  const visit = (id: string) => {
    for (const edge of graph.edges.filter(edge => edge.target === id)) {
      if (edge.source === nodeId || ids.has(edge.source)) continue;
      ids.add(edge.source); visit(edge.source);
    }
  };
  visit(nodeId);
  return graph.nodes.filter(node => ids.has(node.id));
}

export function fromOutput(node: WorkflowNode, port: string): Binding {
  const spec = node.outputs[port];
  return { source: "step", node_id: node.id, path: [port], type: spec.type, required: spec.required !== false };
}

const pythonFileCode = "import pandas as pd\n\ndf = pd.read_excel(inputs['file'])\n# Добавьте обработку данных здесь\ndf.to_csv('/workspace/output/processed.csv', index=False)\nresult = {'rows': len(df), 'columns': list(df.columns)}";

export function connectData(source: WorkflowNode, target: WorkflowNode): WorkflowNode {
  if (Object.keys(target.inputs).length || ["input", "condition", "sql"].includes(target.type)) return target;
  const port = target.type === "python" && source.outputs.file ? "file" : source.outputs.result ? "result" : source.outputs.table ? "table" : Object.keys(source.outputs)[0];
  if (!port) return target;
  const inputs = { [port]: fromOutput(source, port) };
  if (["result", "approval", "parallel", "join"].includes(target.type)) {
    return { ...target, inputs, outputs: { [port]: { ...source.outputs[port] } } };
  }
  return { ...target, inputs, ...(target.type === "python" && port === "file" && target.code === newNode("python", "sample").code ? { code: pythonFileCode } : {}) };
}

export function templateGraph(template: Template, connector: string, agent: string): Pick<Draft, "workflow" | "layout"> {
  const input = newNode("input", "input");
  const sql = { ...newNode("sql", "sql"), connector_name: connector };
  const python = { ...newNode("python", "python"), inputs: { file: fromOutput(sql, "file") }, code: pythonFileCode };
  const llm = { ...newNode("llm", "agent"), agent_id: agent };
  const result = newNode("result", "result");
  let nodes: WorkflowNode[];
  let parameters: WorkflowDefinition["parameters"] = {};
  if (template === "dbt") {
    const coding = newNode("coding_task", "coding");
    parameters = { task: { type: "string", title: "Задание на разработку dbt", required: true } };
    coding.inputs = { task: { source: "input", type: "string", path: ["task"], required: true } };
    result.inputs = Object.fromEntries(Object.keys(coding.outputs).map(port => [port, fromOutput(coding, port)]));
    nodes = [input, coding, result];
  } else if (template === "analysis") {
    llm.inputs = { data: fromOutput(python, "result") };
    result.inputs = { answer_markdown: fromOutput(llm, "result"), files: fromOutput(python, "files") };
    nodes = [input, sql, python, llm, result];
  } else if (template === "sql") {
    result.inputs = { table: fromOutput(sql, "table"), file: fromOutput(sql, "file") };
    nodes = [input, sql, result];
  } else {
    parameters = { question: { type: "string", title: "Вопрос агенту", required: true } };
    llm.inputs = { question: { source: "input", type: "string", path: ["question"], required: true } };
    result.inputs = { answer_markdown: fromOutput(llm, "result") };
    nodes = [input, llm, result];
  }
  result.outputs = Object.fromEntries(Object.entries(result.inputs).map(([key, binding]) => [key, { type: binding.type, required: binding.required }]));
  return { workflow: { schema_version: template === "dbt" ? 2 : 1, parameters, nodes, edges: nodes.slice(1).map((node, i) => ({ source: nodes[i].id, target: node.id })), allowed_tools: [] }, layout: { positions: Object.fromEntries(nodes.map((node, i) => [node.id, { x: 60 + i * 300, y: 130 }])) } };
}

export function insertStep(draft: Draft, selected: string, type: NodeType, capabilities: Capabilities): { draft: Draft; id: string } {
  const id = `${type}_${crypto.randomUUID().slice(0, 8)}`;
  let node = newNode(type, id);
  if (type === "sql") node.connector_name = capabilities.database_connectors[0]?.name ?? "";
  if (type === "llm") node.agent_id = capabilities.agents[0]?.id ?? "";
  const source = draft.workflow.nodes.find(item => item.id === selected);
  const outgoing = draft.workflow.edges.filter(edge => edge.source === selected);
  const sequential = source && !["condition", "parallel", "result"].includes(source.type) && outgoing.length <= 1 && !["input", "condition", "parallel"].includes(type);
  let edges = draft.workflow.edges;
  if (sequential) {
    node = connectData(source, node);
    edges = [...edges.filter(edge => edge.source !== selected), { source: selected, target: id }, ...outgoing.map(edge => ({ ...edge, source: id }))];
  }
  const origin = draft.layout.positions[selected] ?? { x: 60, y: 100 };
  const positions = { ...draft.layout.positions, [id]: { x: origin.x + (sequential ? 300 : 80), y: origin.y + (sequential ? 0 : 200) } };
  if (sequential) {
    for (const item of draft.workflow.nodes) {
      const position = positions[item.id];
      if (position && position.x > origin.x) positions[item.id] = { ...position, x: position.x + 300 };
    }
  }
  return { id, draft: { ...draft, workflow: { ...draft.workflow, schema_version: type === "coding_task" ? 2 : draft.workflow.schema_version, nodes: [...draft.workflow.nodes, node], edges }, layout: { ...draft.layout, positions } } };
}
