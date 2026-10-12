"use client";
import { useRef, useState } from "react";
import { useQuery, useQueries } from "@tanstack/react-query";
import { Button, Input, Textarea } from "@rhetorica/ui";
import { ArrowRight, CheckCircle2, Layers, ListChecks, LoaderCircle, Play, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { codingRequest, missingCodingSettings, type CodingResources } from "@/features/coding/codingApi";
import { createConversation } from "@/features/chat/historyApi";
import { deleteStagedFile, MAX_FILES_PER_TURN, uploadFile, validateSelectedFile, type FileArtifactRef } from "@/features/files/fileApi";
import { artifactFiles, ScenarioFileParameter, scenarioFileError } from "./ScenarioFileParameter";
import { selectClass } from "./WorkflowNodeSettings";
import { WorkflowNavigation } from "./WorkflowNavigation";
import { listScenarios, scenarioListQueryKey } from "./scenariosApi";
import { valueLabels } from "./workflowAuthoring";
import { nodeLabels, startWorkflow, workflowRequest, type Version } from "./workflowApi";

export function WorkflowLaunch({ scenarioId, workspace }: { scenarioId: string; workspace: string }) {
  const query = useQuery({ queryKey: ["scenario-versions", scenarioId, workspace], queryFn: () => workflowRequest<Version[]>(`scenarios/${scenarioId}/versions?workspace_id=${encodeURIComponent(workspace)}`) });
  const catalog = useQuery({ queryKey: scenarioListQueryKey(workspace), queryFn: () => listScenarios(workspace) });
  const scenario = catalog.data?.find(item => item.id === scenarioId);
  const [selected, setSelected] = useState(0);
  const versions = [...(query.data ?? [])].sort((a, b) => b.version - a.version);
  const version = versions.find(item => item.version === selected) ?? versions[0];
  return <main className="min-h-screen bg-[radial-gradient(ellipse_at_top_left,rgb(var(--color-cyan)/0.08),transparent_55%)] bg-surface px-4 py-6 text-primary sm:px-8 sm:py-8">
    <div className="mx-auto max-w-6xl space-y-8">
      <WorkflowNavigation current="Запуск сценария" />
      <header className="flex items-start gap-4">
        <span className="hidden rounded-2xl border border-cyan-border/50 bg-cyan-subtle/40 p-4 text-cyan sm:block"><Play size={25} aria-hidden="true" /></span>
        <div className="min-w-0"><p className="text-xs font-medium uppercase tracking-widest text-cyan">Запуск сценария</p><h1 className="mt-2 break-words text-2xl font-semibold tracking-tight sm:text-3xl">{scenario?.title ?? "Подготовка к запуску"}</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-muted">{scenario?.description || scenario?.summary || "Настройте параметры и запустите сценарий. Ход выполнения и результаты появятся на следующем экране"}</p></div>
      </header>
      {query.isPending && <div role="status" className="flex items-center gap-3 rounded-2xl border border-border bg-elevated/40 p-8 text-muted"><LoaderCircle size={18} className="animate-spin" />Загружаю настройки сценария…</div>}
      {query.error && <div role="alert" className="space-y-3 rounded-2xl border border-danger-border/60 bg-danger-subtle/20 p-6"><p className="text-danger">{query.error.message}</p><Button variant="ghost" onClick={() => void query.refetch()}>Повторить загрузку</Button></div>}
      {!query.isPending && !query.error && !version && <div className="rounded-2xl border border-border bg-elevated/40 p-8"><Layers className="mb-4 text-muted" /><h2 className="font-medium">Пока нет опубликованных версий</h2><p className="mt-2 text-sm text-muted">Сценарий станет доступен для запуска после публикации автором.</p></div>}
      {version && <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <section className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-elevated/40 px-5 py-3"><label htmlFor="launch-version" className="flex items-center gap-2 text-sm text-secondary"><Layers size={16} className="text-muted" />Версия сценария</label><select id="launch-version" className={`${selectClass} sm:!w-auto`} disabled={versions.length === 1} value={version.version} onChange={event => setSelected(Number(event.target.value))}>{versions.map((item, index) => <option key={item.id} value={item.version}>Версия {item.version}{index === 0 ? " · последняя" : ""}</option>)}</select></div>
          <LaunchForm key={version.id} scenarioId={scenarioId} workspace={workspace} version={version} />
        </section>
        <aside className="space-y-5 lg:sticky lg:top-8">
          <section className="rounded-2xl border border-border bg-surface p-5"><h2 className="flex items-center gap-2 text-sm font-semibold"><ListChecks size={17} className="text-cyan" />Что выполнит сценарий<span className="ml-auto text-xs font-normal text-muted">{version.workflow.nodes.length} шагов</span></h2><p className="mt-2 text-xs leading-5 text-muted">Шаги выбранной версии. Связи и условия определяют порядок выполнения.</p><ol className="mt-5 space-y-4">{version.workflow.nodes.map((node, index) => <li key={node.id} className="flex items-start gap-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border border-border-strong bg-hover/60 text-[11px] text-muted">{index + 1}</span><div className="min-w-0"><p className="break-words text-sm text-primary">{node.title}</p><p className="mt-0.5 text-xs text-muted">{nodeLabels[node.type]}</p></div></li>)}</ol></section>
          <div className="flex items-start gap-3 px-1 text-muted"><ShieldCheck size={19} className="mt-0.5 shrink-0 text-muted" /><p className="text-xs leading-5">Запуск использует ваши права доступа. Результаты и файлы доступны только вам.</p></div>
        </aside>
      </div>}
    </div>
  </main>;
}

export function LaunchForm({ scenarioId, workspace, version }: { scenarioId: string; workspace: string; version: Version }) {
  const router = useRouter();
  const [inputs, setInputs] = useState<Record<string, unknown>>(Object.fromEntries(Object.entries(version.workflow.parameters).filter(([, spec]) => spec.default != null).map(([key, spec]) => [key, spec.default])));
  const [conversationId, setConversationId] = useState<string | null>(null);
  const conversationRequest = useRef<ReturnType<typeof createConversation> | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [requestKey] = useState(() => crypto.randomUUID());
  const codingNodes = version.workflow.nodes.filter(node => node.type === "coding_task");
  const codingResources = useQueries({ queries: codingNodes.map(node => ({ queryKey: ["coding-resources", node.root_agent_id], queryFn: () => codingRequest<CodingResources>(`coding-capabilities?root_agent_id=${encodeURIComponent(node.root_agent_id ?? "")}`) })) });
  const codingIssues = codingNodes.flatMap((node, index) => {
    const resource = codingResources[index];
    return [
      ...(!node.root_agent_id ? ["Координатор"] : []),
      ...missingCodingSettings({ dbt_project_subdir: node.dbt_project_subdir, root_agent_id: node.root_agent_id, developer_agent_id: node.developer_agent_id ?? "", qa_agent_id: node.qa_agent_id ?? "", gitlab_connector_id: node.gitlab_connector_id ?? "", project_id: node.project_id ?? "", target_branch: node.target_branch ?? "", environment_profile_id: node.environment_profile_id ?? "", database_connector_id: node.database_connector_id }, resource.data),
      ...(resource.isPending ? ["Проверка ресурсов"] : []),
      ...(resource.error ? [resource.error.message] : []),
    ].map(issue => `${node.title}: ${issue}`);
  });
  const busy = submitting || Object.values(uploading).some(Boolean);
  const missingFile = Object.entries(version.workflow.parameters).some(([name, spec]) => spec.required !== false && ["file", "files"].includes(spec.type) && !artifactFiles(inputs[name]).length);
  async function upload(name: string, selected: File[]) {
    const spec = version.workflow.parameters[name];
    const current = artifactFiles(inputs[name]);
    const files = spec.type === "files" ? selected.slice(0, MAX_FILES_PER_TURN - current.length) : selected.slice(0, 1);
    setUploading(value => ({ ...value, [name]: true })); setError("");
    const uploaded: FileArtifactRef[] = [];
    try {
      for (const file of files) {
        const issue = validateSelectedFile(file); if (issue) throw new Error(scenarioFileError(issue));
      }
      const conversation = conversationId ?? (await (conversationRequest.current ??= createConversation(workspace, `Сценарий: ${version.workflow.nodes.find(node => node.type === "result")?.title ?? "файлы"}`).catch(error => {
        conversationRequest.current = null;
        throw error;
      }))).id;
      setConversationId(conversation);
      for (const file of files) uploaded.push(await uploadFile({ file, workspaceId: workspace, conversationId: conversation }));
      setInputs(values => ({ ...values, [name]: spec.type === "files" ? [...artifactFiles(values[name]), ...uploaded] : uploaded[0] }));
      if (spec.type === "file") await Promise.allSettled(current.map(file => deleteStagedFile(file.id)));
    } catch (err) {
      if (uploaded.length) setInputs(values => ({ ...values, [name]: spec.type === "files" ? [...artifactFiles(values[name]), ...uploaded] : uploaded[0] }));
      setError(err instanceof Error ? err.message : "Ошибка загрузки");
    } finally { setUploading(value => ({ ...value, [name]: false })); }
  }
  async function remove(name: string, file: FileArtifactRef) {
    setUploading(value => ({ ...value, [name]: true })); setError("");
    try { await deleteStagedFile(file.id); setInputs(values => ({ ...values, [name]: version.workflow.parameters[name].type === "files" ? artifactFiles(values[name]).filter(item => item.id !== file.id) : undefined })); }
    catch (err) { setError(err instanceof Error ? err.message : "Не удалось удалить файл"); }
    finally { setUploading(value => ({ ...value, [name]: false })); }
  }
  const parameters = Object.entries(version.workflow.parameters);
  return <form className="overflow-hidden rounded-2xl border border-border bg-surface" onSubmit={async event => { event.preventDefault(); if (busy || missingFile || codingIssues.length) return; setSubmitting(true); setError(""); try { const response = await startWorkflow(scenarioId, workspace, inputs, { version: version.version, conversation_id: conversationId }, requestKey); router.push(`/scenario-runs/${response.run_id}`); } catch (err) { setError(err instanceof Error ? err.message : "Ошибка запуска"); setSubmitting(false); } }}>
    <div className="border-b border-border px-5 py-5 sm:px-6"><h2 className="font-semibold">Параметры запуска</h2><p className="mt-1.5 text-sm text-muted">{parameters.length ? "Заполните данные для этого запуска. Обязательные поля отмечены *" : "Всё готово. Для этого сценария не нужны входные параметры"}</p></div>
    <fieldset disabled={submitting} className="space-y-5 p-5 disabled:opacity-70 sm:p-6">
      {!parameters.length && <div className="flex items-center gap-3 rounded-xl border border-success-border/40 bg-success-subtle/20 p-4 text-sm text-success"><CheckCircle2 size={19} />Можно запускать сразу</div>}
      {parameters.map(([name, spec]) => <div key={name} className="space-y-2">{["file", "files"].includes(spec.type) ? <ScenarioFileParameter id={`parameter-${name}`} label={spec.title || name} multiple={spec.type === "files"} required={spec.required !== false} value={inputs[name]} disabled={submitting} uploading={Boolean(uploading[name])} onSelect={files => void upload(name, files)} onRemove={file => void remove(name, file)} /> : <>
        <label htmlFor={`parameter-${name}`} className="flex items-center justify-between gap-3 text-sm font-medium"><span>{spec.title || name} {spec.required !== false && <span className="text-cyan">*</span>}</span><span className="text-xs font-normal text-muted">{valueLabels[spec.type]}</span></label>
        {spec.type === "boolean" ? <select id={`parameter-${name}`} className={`${selectClass} min-h-11`} value={inputs[name] === undefined ? "" : String(inputs[name])} required={spec.required !== false} onChange={event => setInputs({ ...inputs, [name]: event.target.value === "" ? undefined : event.target.value === "true" })}><option value="">Выберите…</option><option value="true">Да</option><option value="false">Нет</option></select> : ["string", "number"].includes(spec.type) ? <Input id={`parameter-${name}`} className="min-h-11" type={spec.type === "number" ? "number" : "text"} step={spec.type === "number" ? "any" : undefined} required={spec.required !== false} value={String(inputs[name] ?? "")} onChange={event => setInputs({ ...inputs, [name]: spec.type === "number" ? event.target.value === "" ? undefined : Number(event.target.value) : event.target.value })} /> : <Textarea id={`parameter-${name}`} rows={4} className="font-mono text-sm" required={spec.required !== false} defaultValue={inputs[name] == null ? "" : JSON.stringify(inputs[name], null, 2)} placeholder={spec.type === "table" ? '[{"column": "value"}]' : '{"key": "value"}'} onChange={event => {
          try {
            const value: unknown = event.target.value.trim() ? JSON.parse(event.target.value) : undefined;
            event.target.setCustomValidity(spec.required !== false && value == null ? "Укажите значение параметра" : "");
            setInputs(current => ({ ...current, [name]: value }));
          } catch { event.target.setCustomValidity("Введите корректный JSON"); }
        }} />}
      </>}</div>)}
    </fieldset>
    <div className="space-y-4 border-t border-border bg-elevated/40 p-5 sm:p-6">
      {codingIssues.length > 0 && <p role="status" className="text-sm text-warning">Не настроено: {codingIssues.join(", ")}</p>}
      {error && <p role="alert" className="rounded-xl border border-danger-border/50 bg-danger-subtle/30 p-3 text-sm text-danger">{error}</p>}
      <div className="flex flex-wrap items-center justify-between gap-4"><p className="max-w-xs text-xs leading-5 text-muted">{missingFile ? "Прикрепите обязательные файлы для запуска" : "После запуска откроется страница с прогрессом и результатами"}</p><Button className="min-h-11 w-full gap-2 sm:w-auto" disabled={busy || missingFile || codingIssues.length > 0}>{busy ? <LoaderCircle size={16} className="animate-spin" /> : <Play size={16} />} {submitting ? "Запускаю…" : busy ? "Загружаю файлы…" : "Запустить"}{!busy && <ArrowRight size={16} />}</Button></div>
    </div>
  </form>;
}
