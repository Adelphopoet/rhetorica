"use client";

import { useState } from "react";
import { Button, Input } from "@rhetorica/ui";
import type { ValueType, WorkflowNode } from "./workflowApi";
import { valueLabels } from "./workflowAuthoring";

const selectClass = "w-full rounded-lg border border-control-border bg-elevated p-2 text-sm text-primary";
const resultTypes: ValueType[] = ["string", "number", "boolean", "json"];
const jsonType = (type: string) => type === "json" ? {} : { type };
const pythonValue = (type: unknown) => type === "number" || type === "integer" ? "0" : type === "boolean" ? "True" : type === "object" || !type ? "{}" : '"..."';

export function PythonOutputSettings({ node, update }: { node: WorkflowNode; update: (node: WorkflowNode) => void }) {
  const [name, setName] = useState("");
  const [fieldType, setFieldType] = useState<ValueType>("string");
  const result = node.outputs.result ?? { type: "json" as const, required: true };
  const schema = result.json_schema ?? {};
  const properties = schema.properties && typeof schema.properties === "object" && !Array.isArray(schema.properties) ? schema.properties as Record<string, Record<string, unknown>> : {};
  const required = Array.isArray(schema.required) ? schema.required.filter((key): key is string => typeof key === "string") : [];
  const setFields = (fields: typeof properties, names: string[]) => update({ ...node, outputs: { ...node.outputs, result: { ...result, type: "json", json_schema: { ...schema, type: "object", properties: fields, required: names } } } });
  const example = result.type === "json" ? Object.keys(properties).length ? `result = {\n${Object.entries(properties).map(([key, spec]) => `    ${JSON.stringify(key)}: ${pythonValue(spec?.type)},`).join("\n")}\n}` : 'result = {"summary": "Итог обработки"}' : `result = ${pythonValue(result.type)}`;
  return <section className="space-y-3 rounded-xl border border-cyan-border/60 bg-cyan-subtle/10 p-4">
    <h3 className="font-semibold">Результаты шага</h3>
    <p className="text-xs leading-5 text-muted">Присвойте ответ переменной <code>result</code>. Другие шаги смогут выбрать весь ответ или отдельные поля ниже.</p>
    <label className="block space-y-1 text-xs text-muted">Тип результата result<select aria-label="Тип результата Python" className={selectClass} value={result.type} onChange={event => update({ ...node, outputs: { ...node.outputs, result: { ...result, type: event.target.value as ValueType, json_schema: null } } })}>{resultTypes.map(type => <option value={type} key={type}>{valueLabels[type]}</option>)}{!resultTypes.includes(result.type) && <option value={result.type}>{valueLabels[result.type]}</option>}</select></label>
    {result.type === "json" && <div className="space-y-3"><p className="text-xs font-medium">Поля результата</p>
      {!Object.keys(properties).length && <p className="text-xs text-muted">Можно передать JSON целиком или добавить поля, например summary и rows.</p>}
      {Object.entries(properties).map(([key, spec]) => <div key={key} className="space-y-2 rounded-lg border border-border-strong p-3"><div className="flex items-center justify-between"><code className="text-xs">result.{key}</code><button aria-label={`Удалить поле результата ${key}`} className="px-2 text-muted hover:text-danger" onClick={() => setFields(Object.fromEntries(Object.entries(properties).filter(([name]) => name !== key)), required.filter(name => name !== key))}>×</button></div><select aria-label={`Тип поля результата ${key}`} className={selectClass} value={["string", "number", "boolean"].includes(String(spec?.type)) ? String(spec.type) : spec?.type === "integer" ? "number" : "json"} onChange={event => setFields({ ...properties, [key]: jsonType(event.target.value) }, required)}>{resultTypes.map(type => <option value={type} key={type}>{valueLabels[type]}</option>)}</select><label className="flex items-center gap-2 text-xs text-muted"><input type="checkbox" checked={required.includes(key)} onChange={event => setFields(properties, event.target.checked ? [...required, key] : required.filter(name => name !== key))} />Обязательное поле</label></div>)}
      <div className="space-y-2"><Input aria-label="Имя поля результата Python" placeholder="Имя поля, например summary" value={name} onChange={event => setName(event.target.value)} /><div className="flex gap-2"><select aria-label="Тип нового поля результата" className={selectClass} value={fieldType} onChange={event => setFieldType(event.target.value as ValueType)}>{resultTypes.map(type => <option value={type} key={type}>{valueLabels[type]}</option>)}</select><Button size="sm" disabled={!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name) || name in properties} onClick={() => { setFields({ ...properties, [name]: jsonType(fieldType) }, [...required, name]); setName(""); }}>Добавить поле</Button></div></div>
    </div>}
    <div className="space-y-2"><p className="text-xs text-muted">Пример присваивания в вашем коде:</p><pre className="overflow-auto rounded-lg bg-surface p-3 text-xs text-cyan"><code>{example}</code></pre><Button variant="ghost" size="sm" onClick={() => update({ ...node, code: `${node.code ?? ""}\n\n# Результат для следующих шагов\n${example}\n` })}>Вставить пример в код</Button></div>
    <div className="border-t border-border-strong pt-3 text-xs leading-5"><strong>files · Созданные файлы</strong><p className="text-muted">Сохраняйте файлы в <code>/workspace/output</code>. Они автоматически появятся на выходе <code>files</code> и будут доступны следующим шагам.</p></div>
  </section>;
}
