"use client";

import { useRef, useState } from "react";
import { Button, Input } from "@rhetorica/ui";
import Link from "next/link";
import { ThemeSelector } from "@/features/theme/ThemeSelector";
import { useRouter } from "next/navigation";
import { createScenario } from "./scenariosApi";
import { templateGraph, templates, type Template } from "./workflowAuthoring";
import { workflowRequest, type Capabilities, type Draft } from "./workflowApi";

const selectClass = "w-full rounded-lg border border-control-border bg-elevated p-2 text-sm text-primary";

export function NewWorkflowScenario({ workspace, capabilities, initialScope = "personal", canCreateOfficial = false }: { workspace: string; capabilities: Capabilities; initialScope?: "personal" | "official"; canCreateOfficial?: boolean }) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [scope, setScope] = useState<"personal" | "official">(canCreateOfficial ? initialScope : "personal");
  const [template, setTemplate] = useState<Template>("analysis");
  const [connector, setConnector] = useState(capabilities.database_connectors[0]?.name ?? "");
  const [agent, setAgent] = useState(capabilities.agents[0]?.id ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const created = useRef<string | null>(null);
  return <main className="flex min-h-screen items-center justify-center bg-surface p-6 text-primary"><form className="w-full max-w-2xl space-y-5 rounded-2xl border border-border bg-elevated p-8" onSubmit={async event => { event.preventDefault(); setBusy(true); setError(""); try {
    if (!created.current) {
      const scenario = await createScenario({ workspace_id: workspace, scope, title: title.trim(), summary: title.trim(), description: "", prompt_template: "Проанализируй входные данные", input_schema: {}, tool_policy: { allow_run_sql: true, sql_guard_required: true, allowed_tools: [], post_answer_checklist: [] }, default_connector_bindings: { run_sql: true }, status: "draft" });
      created.current = scenario.id;
    }
    const draft = await workflowRequest<Draft>(`scenarios/${created.current}/draft?workspace_id=${encodeURIComponent(workspace)}`);
    await workflowRequest(`scenarios/${created.current}/draft`, "PUT", { workspace_id: workspace, expected_revision: draft.revision, ...templateGraph(template, connector, agent), test_cases: [] });
    router.replace(`/scenarios/${created.current}`);
  } catch (err) { setError(err instanceof Error ? err.message : "Ошибка создания"); setBusy(false); } }}>
    <div className="flex flex-wrap items-center justify-between gap-3"><Link href={initialScope === "official" && canCreateOfficial ? "/admin" : "/"} className="text-sm text-cyan">{initialScope === "official" && canCreateOfficial ? "← Администрирование" : "← Рабочее пространство"}</Link><ThemeSelector /></div><h1 className="text-2xl font-semibold">{scope === "official" ? "Новый общий сценарий" : "Новый сценарий"}</h1><p className="text-sm text-muted">Начните с готовой цепочки. Связи и передача файлов уже настроены.</p>
    <label className="block space-y-2 text-sm">Название<Input autoFocus required maxLength={255} value={title} onChange={event => setTitle(event.target.value)} /></label>
    <fieldset className="space-y-2"><legend className="mb-2 text-sm">Что должен делать сценарий?</legend>{Object.entries(templates).map(([id, item]) => <label key={id} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-4 ${template === id ? "border-cyan bg-cyan-subtle/30" : "border-border-strong"}`}><input type="radio" name="template" className="mt-1" value={id} checked={template === id} onChange={() => setTemplate(id as Template)} /><span><strong className="text-sm">{item.title}</strong><span className="mt-1 block text-xs leading-5 text-muted">{item.description}</span></span></label>)}</fieldset>
    <div className="grid gap-4 sm:grid-cols-2">{template !== "agent" && template !== "dbt" && <label className="space-y-2 text-sm">Коннектор SQL<select required className={selectClass} value={connector} onChange={event => setConnector(event.target.value)}><option value="">Выберите коннектор…</option>{capabilities.database_connectors.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label>}{template !== "sql" && template !== "dbt" && <label className="space-y-2 text-sm">Агент<select required className={selectClass} value={agent} onChange={event => setAgent(event.target.value)}><option value="">Выберите агента…</option>{capabilities.agents.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>}</div>
    <label className="block space-y-2 text-sm">Доступ<select className={selectClass} value={scope} onChange={event => setScope(event.target.value as typeof scope)}><option value="personal">Личный сценарий</option>{canCreateOfficial && <option value="official">Общий · только администратор</option>}</select></label>
    {!capabilities.enabled && <p role="status" className="text-warning">Создание цепочек отключено настройкой сервера.</p>}
    {error && <p role="alert" className="text-danger">{error}</p>}<Button disabled={busy || !title.trim() || !capabilities.enabled}>{busy ? "Создаю цепочку…" : "Создать и настроить"}</Button>
  </form></main>;
}
