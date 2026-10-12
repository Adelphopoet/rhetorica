"use client";

import { ScenarioCodingSettings } from "@/features/coding/ScenarioCodingSettings";
import "./monaco";

import { useState } from "react";
import { ThemedEditor as Editor } from "@/app/components/themed-editor";
import { Button, Input, Textarea } from "@rhetorica/ui";
import { nodeLabels, valueTypes, type Binding, type Capabilities, type ValueSpec, type ValueType, type WorkflowDefinition, type WorkflowNode } from "./workflowApi";

import { valueLabels } from "./workflowAuthoring";

import { availableOutputSources, outputHandle, outputPorts, passthroughInputs, portBinding } from "./workflowPorts";
import { PythonOutputSettings } from "./PythonOutputSettings";

export const selectClass = "w-full rounded-lg border border-control-border bg-elevated p-2 text-sm text-secondary";
const labelClass = "block space-y-1 text-xs text-muted";

export function JsonField({ value, onChange, label, rows = 4 }: { value: unknown; onChange: (value: unknown) => void; label: string; rows?: number }) {
  const serialized = JSON.stringify(value ?? null, null, 2);
  const [edit, setEdit] = useState<{ base: string; text: string; error: string } | null>(null);
  const text = edit?.base === serialized ? edit.text : serialized;
  const error = edit?.base === serialized ? edit.error : "";
  return <label className={labelClass}>{label}<Textarea aria-label={label} rows={rows} className="font-mono text-xs" value={text} onChange={event => setEdit({ base: serialized, text: event.target.value, error: "" })} onBlur={() => { try { onChange(JSON.parse(text)); setEdit(null); } catch { setEdit({ base: serialized, text, error: "Некорректный JSON" }); } }} />{error && <span role="alert" className="text-danger">{error}</span>}</label>;
}

export function ValueField({ type, value, onChange, label }: { type: ValueType; value: unknown; onChange: (value: unknown) => void; label: string }) {
  if (type === "boolean") return <label className={labelClass}>{label}<select aria-label={label} className={selectClass} value={value == null ? "" : String(value)} onChange={event => onChange(event.target.value === "" ? null : event.target.value === "true")}><option value="">Не задано</option><option value="true">Да</option><option value="false">Нет</option></select></label>;
  if (type === "number") return <label className={labelClass}>{label}<Input aria-label={label} type="number" step="any" value={value == null ? "" : String(value)} onChange={event => onChange(event.target.value === "" ? null : Number(event.target.value))} /></label>;
  if (type === "string") return <label className={labelClass}>{label}<Textarea aria-label={label} rows={2} value={String(value ?? "")} onChange={event => onChange(event.target.value)} /></label>;
  if (type === "file") {
    const file = value && typeof value === "object" ? value as Record<string, unknown> : {};
    return <fieldset className="space-y-2"><legend className="text-xs text-muted">{label}</legend><label className={labelClass}>ID сохранённого файла<Input aria-label={`${label}: ID файла`} value={String(file.id ?? "")} onChange={event => onChange({ ...file, id: event.target.value })} /></label><label className={labelClass}>Имя файла<Input aria-label={`${label}: имя файла`} value={String(file.filename ?? "")} onChange={event => onChange({ ...file, filename: event.target.value })} /></label></fieldset>;
  }
  return <JsonField label={label} value={value} onChange={onChange} rows={3} />;
}

export function ParameterSettings({ parameters, onChange }: { parameters: Record<string, ValueSpec>; onChange: (parameters: Record<string, ValueSpec>) => void }) {
  const [name, setName] = useState("");
  return <div className="space-y-3">
    {Object.entries(parameters).map(([key, spec]) => <div key={key} className="space-y-2 rounded-lg border border-border-strong p-3">
      <div className="flex items-center justify-between"><strong className="text-sm">{key}</strong><button aria-label={`Удалить параметр ${key}`} onClick={() => onChange(Object.fromEntries(Object.entries(parameters).filter(([name]) => name !== key)))}>×</button></div>
      <select aria-label={`Тип ${key}`} className={selectClass} value={spec.type} onChange={event => onChange({ ...parameters, [key]: { ...spec, type: event.target.value as ValueType, default: null } })}>{valueTypes.map(type => <option key={type} value={type}>{valueLabels[type]}</option>)}</select>
      <label className="flex gap-2 text-xs"><input type="checkbox" checked={spec.required !== false} onChange={event => onChange({ ...parameters, [key]: { ...spec, required: event.target.checked } })} />Обязательный</label>
      <ValueField key={`${key}-${spec.type}`} type={spec.type} label={`Значение по умолчанию ${key}`} value={spec.default} onChange={value => onChange({ ...parameters, [key]: { ...spec, default: value } })} />
    </div>)}
    <div className="flex gap-2"><Input aria-label="Имя параметра" placeholder="Имя параметра" value={name} onChange={event => setName(event.target.value)} /><Button size="sm" disabled={!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name) || name in parameters} onClick={() => { onChange({ ...parameters, [name]: { type: "string", required: true } }); setName(""); }}>+</Button></div>
  </div>;
}

export function Bindings({ node, graph, update }: { node: WorkflowNode; graph: WorkflowDefinition; update: (node: WorkflowNode) => void }) {
  const [name, setName] = useState("");
  const previous = availableOutputSources(graph, node.id);
  const inputNode = graph.nodes.find(item => item.type === "input");
  const optionId = (source: string, path: (string | number)[] = []) => `${source}:${path.length > 1 ? outputHandle(path) : path[0]}`;
  const changeInputs = (inputs: Record<string, Binding>) => update(passthroughInputs(node, inputs));
  const set = (key: string, binding: Binding) => changeInputs({ ...node.inputs, [key]: binding });
  const available = [
    ...(inputNode ? outputPorts(inputNode, graph.parameters).map(port => ({ id: optionId("input", port.path), label: `Параметр · ${port.label} · ${valueLabels[port.type]}`, name: port.path.at(-1)!, binding: portBinding(inputNode, port) })) : []),
    ...previous.flatMap(item => outputPorts(item).map(port => ({ id: optionId(item.id, port.path), label: `${item.title} → ${port.label} · ${valueLabels[port.type]}`, name: port.path.at(-1)!, binding: portBinding(item, port) }))),
  ];
  const add = (base: string, binding: Binding) => {
    let key = base; let count = 2;
    while (key in node.inputs) key = `${base}_${count++}`;
    set(key, binding);
  };
  return <section className="space-y-3"><h3 className="text-sm font-semibold">Входы шага</h3>
    <p className="text-xs leading-5 text-muted">Выберите данные, которые получит этот шаг. Шаги можно связать здесь или перетащить выход в «+ Вход» на холсте. Связь и порядок выполнения настроятся автоматически. Константа хранится внутри шага и не создаёт стрелку.{node.type === "python" && <> В коде обращайтесь по имени: <code>inputs[&quot;file&quot;]</code>.</>}</p>
    <select aria-label="Добавить данные предыдущего шага" className={selectClass} value="" onChange={event => { const item = available.find(item => item.id === event.target.value); if (item) add(item.name, item.binding); }}><option value="">+ Выбрать параметр или результат…</option>{available.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
    {!available.length && <p className="text-xs text-muted">Добавьте шаг-источник или параметр сценария. Источники, создающие цикл, недоступны.</p>}
    {Object.entries(node.inputs).map(([key, binding]) => <div key={key} className="space-y-3 rounded-xl border border-border-strong bg-elevated/50 p-3">
      <div className="flex items-end gap-2"><label className={`${labelClass} min-w-0 flex-1`}>Имя в шаге<Input aria-label={`Имя входа ${key}`} defaultValue={key} onBlur={event => { const next = event.target.value.trim(); if (/^[a-zA-Z][a-zA-Z0-9_]*$/.test(next) && next !== key && !(next in node.inputs)) changeInputs(Object.fromEntries(Object.entries(node.inputs).map(([name, value]) => [name === key ? next : name, value]))); else event.target.value = key; }} /></label><button className="p-2 text-muted hover:text-danger" aria-label={`Удалить вход ${key}`} onClick={() => changeInputs(Object.fromEntries(Object.entries(node.inputs).filter(([name]) => name !== key)))}>×</button></div>
      <label className={labelClass}>Откуда взять значение<select aria-label={`Источник ${key}`} className={selectClass} value={binding.source === "constant" ? "constant" : optionId(binding.source === "input" ? "input" : binding.node_id ?? "", binding.path)} onChange={event => { const item = available.find(item => item.id === event.target.value); set(key, item ? item.binding : { source: "constant", type: binding.type, value: null, required: true }); }}>
        <option value="constant">Задать значение</option>{available.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        {binding.source !== "constant" && !available.some(item => item.id === optionId(binding.source === "input" ? "input" : binding.node_id ?? "", binding.path)) && <option value={optionId(binding.source === "input" ? "input" : binding.node_id ?? "", binding.path)}>Недоступная ссылка · {binding.node_id}.{binding.path?.[0]}</option>}
      </select></label>
      {binding.source === "constant" ? <><label className={labelClass}>Тип значения<select aria-label={`Тип входа ${key}`} className={selectClass} value={binding.type} onChange={event => set(key, { ...binding, type: event.target.value as ValueType, value: null })}>{valueTypes.map(type => <option key={type} value={type}>{valueLabels[type]}</option>)}</select></label><ValueField type={binding.type} label={`Значение ${key}`} value={binding.value} onChange={value => set(key, { ...binding, value })} /></> : <p className="rounded-lg bg-cyan-subtle px-3 py-2 text-xs text-cyan">{valueLabels[binding.type]}{binding.type === "file" ? " · передаётся полный файл" : ""}</p>}
      <details className="text-xs text-muted"><summary className="cursor-pointer">Дополнительные настройки</summary><div className="mt-3 space-y-3">
        <label className="flex gap-2"><input type="checkbox" checked={binding.required !== false} onChange={event => set(key, { ...binding, required: event.target.checked, default: null })} />Обязательный вход</label>
        {binding.required === false && <ValueField type={binding.type} label={`Если ветка пропущена: ${key}`} value={binding.default} onChange={value => set(key, { ...binding, default: value })} />}
        {binding.source !== "constant" && <><JsonField label={`Вложенный путь ${key}`} value={binding.path?.slice(1) ?? []} onChange={value => { if (Array.isArray(value) && value.every(part => typeof part === "string" || typeof part === "number")) set(key, { ...binding, path: [binding.path?.[0] ?? "", ...value] }); }} rows={1} /><label className={labelClass}>Тип вложенного значения<select aria-label={`Тип вложенного входа ${key}`} className={selectClass} value={binding.type} onChange={event => set(key, { ...binding, type: event.target.value as ValueType })}>{valueTypes.map(type => <option key={type} value={type}>{valueLabels[type]}</option>)}</select></label></>}
      </div></details>
    </div>)}
    <div className="flex gap-2"><Input aria-label="Имя нового входа" placeholder="Имя, например limit" value={name} onChange={event => setName(event.target.value)} /><Button size="sm" variant="ghost" disabled={!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name) || name in node.inputs} onClick={() => { add(name, { source: "constant", type: "string", value: "", required: true }); setName(""); }}>+ Значение</Button></div>
  </section>;
}

export function WorkflowNodeSettings({ node, graph, capabilities, update, updateParameters }: { node: WorkflowNode; graph: WorkflowDefinition; capabilities: Capabilities; update: (node: WorkflowNode) => void; updateParameters: (parameters: Record<string, ValueSpec>) => void }) {
  const selectedAgent = capabilities.agents.find(agent => agent.id === node.agent_id);
  return <div className="space-y-5">
    <div><p className="text-xs uppercase tracking-wider text-cyan">{nodeLabels[node.type]}</p><h2 className="mt-1 font-semibold">Настройки шага</h2><p className="mt-1 font-mono text-xs text-muted">{node.id}</p></div>
    <label className={labelClass}>Название<Input value={node.title} onChange={event => update({ ...node, title: event.target.value })} /></label>
    {node.type === "input" && <ParameterSettings parameters={graph.parameters} onChange={updateParameters} />}
    {node.type === "sql" && <>
      <label className={labelClass}>Коннектор<select className={selectClass} value={node.connector_name} onChange={event => update({ ...node, connector_name: event.target.value })}><option value="">Выберите коннектор…</option>{capabilities.database_connectors.map(item => <option key={item.name} value={item.name}>{item.name} · {item.engine}</option>)}</select></label>
      <p className="text-xs text-muted">Параметры записываются как <code>:name</code>. Результат сохраняется в XLSX; выход <code>file</code> передаёт весь файл.</p>
      <Editor height="240px" language="sql" value={node.sql} onChange={value => update({ ...node, sql: value ?? "" })} options={{ minimap: { enabled: false }, fontSize: 12, wordWrap: "on", ariaLabel: "SQL шага" }} />
      <div className="grid grid-cols-2 gap-2"><label className={labelClass}>Лимит строк<Input type="number" min={1} max={1048575} value={node.row_limit ?? 5000} onChange={event => update({ ...node, row_limit: Number(event.target.value) })} /></label><label className={labelClass}>Таймаут, ms<Input type="number" min={100} max={900000} value={node.timeout_ms ?? 300000} onChange={event => update({ ...node, timeout_ms: Number(event.target.value) })} /></label></div>
    </>}
    {node.type === "python" && <><p className="text-xs text-muted">В <code>inputs</code> доступны значения и пути к файлам. SQL-файл: <code>pd.read_excel(inputs[&quot;file&quot;])</code>. Запишите ответ в <code>result</code>, файлы — в <code>/workspace/output</code>. Тип ответа и поля задаются ниже.</p><Editor height="300px" language="python" value={node.code} onChange={value => update({ ...node, code: value ?? "" })} options={{ minimap: { enabled: false }, fontSize: 12, wordWrap: "on", ariaLabel: "Python шага" }} /></>}
    {node.type === "llm" && <>
      <label className={labelClass}>Агент<select className={selectClass} value={node.agent_id} onChange={event => update({ ...node, agent_id: event.target.value })}><option value="">Выберите агента…</option>{capabilities.agents.map(agent => <option key={agent.id} value={agent.id}>{agent.title}</option>)}</select></label>
      <label className={labelClass}>Инструкции<Textarea rows={6} value={node.instructions} onChange={event => update({ ...node, instructions: event.target.value })} /></label>
      <label className={labelClass}>Ответ<select className={selectClass} value={node.output_format} onChange={event => update({ ...node, output_format: event.target.value as "text" | "json", outputs: { result: { type: event.target.value === "json" ? "json" : "string", ...(event.target.value === "json" ? { json_schema: { type: "object" } } : {}) } } })}><option value="text">Текст</option><option value="json" disabled={!selectedAgent?.structured_output}>Структурированный JSON</option></select></label>
      {!selectedAgent?.structured_output && <p className="text-xs text-warning">Выбранный коннектор не поддерживает структурированный ответ.</p>}
      <fieldset className="space-y-2"><legend className="mb-2 text-xs text-muted">Инструменты шага</legend>{capabilities.tools.map(tool => <label key={tool.name} className="flex items-start gap-2 text-xs"><input type="checkbox" checked={node.allowed_tools?.includes(tool.name) ?? false} onChange={event => update({ ...node, allowed_tools: event.target.checked ? [...(node.allowed_tools ?? []), tool.name] : node.allowed_tools?.filter(name => name !== tool.name) })} />{tool.description}</label>)}</fieldset>
    </>}
    {node.type === "coding_task" && <ScenarioCodingSettings node={node} capabilities={capabilities} update={update} />}
    {node.type === "approval" && <label className={labelClass}>Запрос инициатору<Textarea value={node.message} onChange={event => update({ ...node, message: event.target.value })} /></label>}
    {node.type === "condition" && <div className="space-y-2"><select className={selectClass} aria-label="Соединение условий" value={node.match} onChange={event => update({ ...node, match: event.target.value as "all" | "any" })}><option value="all">Все условия</option><option value="any">Любое условие</option></select>{node.rules?.map((rule, index) => <div key={index} className="space-y-2 rounded-lg border border-border-strong p-2"><select aria-label="Вход условия" className={selectClass} value={rule.input} onChange={event => update({ ...node, rules: node.rules?.map((item, i) => i === index ? { ...item, input: event.target.value } : item) })}>{Object.keys(node.inputs).map(name => <option key={name}>{name}</option>)}</select><select aria-label="Оператор" className={selectClass} value={rule.operator} onChange={event => update({ ...node, rules: node.rules?.map((item, i) => i === index ? { ...item, operator: event.target.value as typeof rule.operator } : item) })}>{["eq", "ne", "gt", "gte", "lt", "lte", "contains", "is_empty"].map(operator => <option key={operator}>{operator}</option>)}</select><JsonField label="Сравнить со значением" value={rule.value} onChange={value => update({ ...node, rules: node.rules?.map((item, i) => i === index ? { ...item, value } : item) })} rows={2} /></div>)}<Button size="sm" variant="ghost" onClick={() => update({ ...node, rules: [...(node.rules ?? []), { input: Object.keys(node.inputs)[0] ?? "value", operator: "eq", value: true }] })}>Добавить условие</Button></div>}
    {node.type === "python" && <PythonOutputSettings node={node} update={update} />}
    {node.type !== "input" && <Bindings node={node} graph={graph} update={update} />}
    {node.type !== "input" && <details className="border-t border-border-strong pt-3"><summary className="cursor-pointer text-sm">Расширенные настройки выходов · JSON</summary><JsonField key={JSON.stringify(Object.keys(node.outputs)) + node.output_format} label="Выходные порты" value={node.outputs} onChange={value => { if (value && typeof value === "object" && !Array.isArray(value)) update({ ...node, outputs: value as Record<string, ValueSpec> }); }} rows={10} /></details>}
  </div>;
}
