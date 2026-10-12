"use client";

import "./monaco";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type Connection, type Edge, type Node, type Viewport } from "@xyflow/react";
import { connectWorkflow, deleteWorkflowElements, updateNodeBindings } from "./workflowConnections";
import { nodeColors, nodeDescriptions, nodeIcons } from "./workflowNodePresentation";
import { WorkflowCanvas } from "./WorkflowCanvas";
import { insertStep, templateGraph, templates, type Template } from "./workflowAuthoring";
import { ThemedDiffEditor as DiffEditor } from "@/app/components/themed-editor";
import { Button, Textarea } from "@rhetorica/ui";
import { ArrowLeft, Check, Copy, GitBranch, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Play, Redo2, Save, Sparkles, Undo2 } from "lucide-react";
import Link from "next/link";
import { ThemeSelector } from "@/features/theme/ThemeSelector";
import { createConversation } from "@/features/chat/historyApi";
import { deleteStagedFile, MAX_FILES_PER_TURN, uploadFile, validateSelectedFile, type FileArtifactRef } from "@/features/files/fileApi";
import { NewWorkflowScenario } from "./NewWorkflowScenario";
import { artifactFiles, ScenarioFileParameter, scenarioFileError } from "./ScenarioFileParameter";
import { JsonField, selectClass, ValueField, WorkflowNodeSettings } from "./WorkflowNodeSettings";
import { WorkflowProgress } from "./WorkflowProgress";
import { requestWorkflowProposal, type AiProgress } from "./workflowProposalApi";
import { WorkflowRunView } from "./WorkflowRunView";
import { nodeLabels, startWorkflow, workflowRequest, type AiProposal, type Capabilities, type Draft, type NodeType, type Validation, type Version, type WorkflowNode } from "./workflowApi";

export function WorkflowEditor({ scenarioId, workspace, initialScope = "personal", canCreateOfficial = false }: { scenarioId: string; workspace: string; initialScope?: "personal" | "official"; canCreateOfficial?: boolean }) {
  const draft = useQuery({ queryKey: ["scenario-draft", scenarioId, workspace], enabled: scenarioId !== "new", queryFn: () => workflowRequest<Draft>(`scenarios/${scenarioId}/draft?workspace_id=${encodeURIComponent(workspace)}`) });
  const capabilities = useQuery({ queryKey: ["scenario-capabilities", workspace], queryFn: () => workflowRequest<Capabilities>(`scenario-capabilities?workspace_id=${encodeURIComponent(workspace)}`) });
  if (scenarioId === "new" && capabilities.data) return <NewWorkflowScenario workspace={workspace} capabilities={capabilities.data} initialScope={initialScope} canCreateOfficial={canCreateOfficial} />;
  if (!draft.data || !capabilities.data) return <main className="min-h-screen bg-page p-10 text-secondary"><Link href="/">← Рабочее пространство</Link><p className="mt-6" role="status">{draft.error?.message ?? capabilities.error?.message ?? "Загружаю редактор…"}</p></main>;
  return <EditorLoaded key={scenarioId} initial={draft.data} workspace={workspace} capabilities={capabilities.data} />;
}

function EditorLoaded({ initial, workspace, capabilities }: { initial: Draft; workspace: string; capabilities: Capabilities }) {
  const client = useQueryClient();
  const [history, setHistory] = useState<{ past: Draft[]; present: Draft; future: Draft[] }>({ past: [], present: initial, future: [] });
  const draft = history.present;
  const [revision, setRevision] = useState(initial.revision);
  const [saved, setSaved] = useState(JSON.stringify(initial));
  const [selected, setSelected] = useState(initial.workflow.nodes[0]?.id ?? "");
  const [busy, setBusy] = useState("");
  const [busySince, setBusySince] = useState(0);

  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [validation, setValidation] = useState<Validation | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(true);
  const [debug, setDebug] = useState(false);
  const [testInputs, setTestInputs] = useState<Record<string, unknown>>({});
  const [expected, setExpected] = useState<Record<string, unknown> | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [testNode, setTestNode] = useState(false);
  const [testKey, setTestKey] = useState(0);
  const [testFileBusy, setTestFileBusy] = useState("");
  const [aiOpen, setAiOpen] = useState(false);
  const [aiText, setAiText] = useState("");
  const [aiAgent, setAiAgent] = useState(capabilities.agents[0]?.id ?? "");
  const [proposalBase, setProposalBase] = useState("");
  const [proposal, setProposal] = useState<AiProposal | null>(null);
  const [aiProgress, setAiProgress] = useState<AiProgress | null>(null);
  const aiRequest = useRef<AbortController | null>(null);
  useEffect(() => () => aiRequest.current?.abort(), []);
  const versionQuery = useQuery({ queryKey: ["scenario-versions", draft.scenario_id, workspace], queryFn: () => workflowRequest<Version[]>(`scenarios/${draft.scenario_id}/versions?workspace_id=${encodeURIComponent(workspace)}`) });
  const currentNode = draft.workflow.nodes.find(node => node.id === selected);
  const changed = JSON.stringify(draft) !== saved;
  const commit = useCallback((next: Draft) => setHistory(state => ({ past: [...state.past.slice(-49), state.present], present: next, future: [] })), []);
  const undo = useCallback(() => setHistory(state => state.past.length ? { past: state.past.slice(0, -1), present: state.past.at(-1)!, future: [state.present, ...state.future] } : state), []);
  const redo = useCallback(() => setHistory(state => state.future.length ? { past: [...state.past, state.present], present: state.future[0], future: state.future.slice(1) } : state), []);
  const updateNode = (node: WorkflowNode) => { commit({ ...draft, workflow: updateNodeBindings(draft.workflow, node) }); setValidation(null); };
  const copyNode = () => { if (!currentNode || currentNode.type === "input" || currentNode.type === "result") return; const id = `${currentNode.type}_${crypto.randomUUID().slice(0, 8)}`; const position = draft.layout.positions[currentNode.id] ?? { x: 250, y: 150 }; commit({ ...draft, workflow: { ...draft.workflow, nodes: [...draft.workflow.nodes, { ...structuredClone(currentNode), id, title: `${currentNode.title} (копия)` }] }, layout: { ...draft.layout, positions: { ...draft.layout.positions, [id]: { x: position.x + 60, y: position.y + 140 } } } }); setSelected(id); };
  async function work(name: string, action: () => Promise<void>) { setBusy(name); setBusySince(Date.now()); setError(""); setNotice(""); try { await action(); } catch (err) { if (err instanceof Error && err.name === "AbortError") setNotice("Запрос остановлен. Черновик не изменён"); else setError(err instanceof Error ? err.message : "Операция не удалась"); } finally { setBusy(""); } }
  const save = useCallback(async () => {
    const response = await workflowRequest<Draft>(`scenarios/${draft.scenario_id}/draft`, "PUT", { workspace_id: workspace, expected_revision: revision, workflow: draft.workflow, layout: draft.layout, test_cases: draft.test_cases });
    setRevision(response.revision); setSaved(JSON.stringify(draft)); setNotice(`Черновик сохранён · ревизия ${response.revision}`); return response;
  }, [draft, revision, workspace]);
  const actionRef = useRef({ save, copyNode });
  useEffect(() => { actionRef.current = { save, copyNode }; });
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!event.metaKey && !event.ctrlKey) return;
      if (event.key.toLowerCase() === "s") { event.preventDefault(); void actionRef.current.save().catch(err => setError(String(err))); return; }
      const target = event.target as HTMLElement;
      if (target.closest("input,textarea,[contenteditable=true],.monaco-editor")) return;
      if (event.key.toLowerCase() === "z") { event.preventDefault(); if (event.shiftKey) redo(); else undo(); }
      if (event.key.toLowerCase() === "d") { event.preventDefault(); actionRef.current.copyNode(); }
    };
    window.addEventListener("keydown", handler); return () => window.removeEventListener("keydown", handler);
  }, [undo, redo]);
  useEffect(() => {
    if (!changed) return;
    const handler = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", handler); return () => window.removeEventListener("beforeunload", handler);
  }, [changed]);
  const moveNodes = useCallback((positions: Draft["layout"]["positions"]) => setHistory(state => ({ past: [...state.past.slice(-49), state.present], future: [], present: { ...state.present, layout: { ...state.present.layout, positions: { ...state.present.layout.positions, ...positions } } } })), []);
  const moveViewport = useCallback((viewport: Viewport) => setHistory(state => ({ ...state, present: { ...state.present, layout: { ...state.present.layout, viewport } } })), []);
  const deleteNodes = useCallback(({ nodes, edges }: { nodes: Node[]; edges: Edge[] }) => {
    setHistory(state => {
      return { past: [...state.past.slice(-49), state.present], future: [], present: { ...state.present, workflow: deleteWorkflowElements(state.present.workflow, { nodes, edges }) } };
    });
  }, []);
  const connect = useCallback((connection: Connection) => {
    try {
      const workflow = connectWorkflow(draft.workflow, connection);
      if (workflow !== draft.workflow) commit({ ...draft, workflow });
      setSelected(connection.target); setValidation(null); setError("");
    } catch (error) { setError(error instanceof Error ? error.message : "Не удалось соединить шаги"); }
  }, [draft, commit]);
  const addNode = (type: NodeType) => { const next = insertStep(draft, selected, type, capabilities); commit(next.draft); setSelected(next.id); setValidation(null); };
  const run = (published: boolean) => work("Запускаю", async () => { const latest = published ? undefined : await save(); const response = await startWorkflow(draft.scenario_id, workspace, testInputs, { conversation_id: conversationId, ...(latest ? { draft_revision: latest.revision, ...(testNode && currentNode ? { test_node_id: currentNode.id, test_inputs: testInputs } : {}) } : {}), expected_schema: expected }); setConversationId(response.run.conversation_id); setRunId(response.run_id); setDebug(true); });
  const parameterSpecs = testNode && currentNode?.type !== "input" ? currentNode?.inputs ?? {} : draft.workflow.parameters;
  const parameterNames = Object.keys(parameterSpecs);
  const uploadTestFiles = async (name: string, selectedFiles: File[]) => {
    setTestFileBusy(name);
    await work("Загружаю файл", async () => {
    const spec = parameterSpecs[name];
    const current = artifactFiles(testInputs[name]);
    const selected = spec.type === "files" ? selectedFiles.slice(0, MAX_FILES_PER_TURN - current.length) : selectedFiles.slice(0, 1);
    for (const file of selected) {
      const issue = validateSelectedFile(file);
      if (issue) throw new Error(scenarioFileError(issue));
    }
    const conversation = conversationId ?? (await createConversation(workspace, "Тест сценария")).id;
    setConversationId(conversation);
    const uploaded: FileArtifactRef[] = [];
    try {
      for (const file of selected) uploaded.push(await uploadFile({ file, workspaceId: workspace, conversationId: conversation }));
    } catch (error) {
      if (uploaded.length) {
        setTestInputs(inputs => ({ ...inputs, [name]: spec.type === "files" ? [...artifactFiles(inputs[name]), ...uploaded] : uploaded[0] }));
        setTestKey(value => value + 1);
      }
      throw error;
    }
    setTestInputs(inputs => ({ ...inputs, [name]: spec.type === "files" ? [...artifactFiles(inputs[name]), ...uploaded] : uploaded[0] }));
    setTestKey(value => value + 1);
    if (spec.type === "file") await Promise.allSettled(current.map(file => deleteStagedFile(file.id)));
    });
    setTestFileBusy("");
  };
  const removeTestFile = async (name: string, file: FileArtifactRef) => {
    setTestFileBusy(name);
    await work("Удаляю файл", async () => {
      await deleteStagedFile(file.id);
      setTestInputs(inputs => ({ ...inputs, [name]: parameterSpecs[name].type === "files" ? artifactFiles(inputs[name]).filter(item => item.id !== file.id) : undefined }));
      setTestKey(value => value + 1);
    });
    setTestFileBusy("");
  };
  return <main className="flex h-screen min-h-[640px] flex-col overflow-hidden bg-page text-secondary">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border-strong bg-surface px-5 py-3">
      <div className="flex items-center gap-4"><Link href="/" aria-label="На главную" className="flex min-h-10 items-center gap-2 rounded-lg px-2 text-sm text-muted hover:bg-elevated hover:text-primary"><ArrowLeft size={18} /><span className="hidden sm:inline">На главную</span></Link><div><h1 className="text-sm font-semibold">Редактор сценария</h1><p className="text-[11px] text-muted">{changed ? "● Есть несохранённые изменения" : "Черновик сохранён"} · r{revision}</p></div></div>
      <div className="flex flex-wrap items-center gap-2"><ThemeSelector /><Button size="sm" variant="ghost" aria-label="Отменить изменение" disabled={!history.past.length} onClick={undo}><Undo2 size={15} /></Button><Button size="sm" variant="ghost" aria-label="Повторить изменение" disabled={!history.future.length} onClick={redo}><Redo2 size={15} /></Button><Button size="sm" variant="ghost" onClick={() => setAiOpen(true)}><Sparkles size={15} /> AI-помощь</Button><Button size="sm" variant="ghost" disabled={!!busy} onClick={() => void work("Проверяю", async () => { const result = await workflowRequest<Validation>(`scenarios/${draft.scenario_id}/validate`, "POST", { workspace_id: workspace, expected_revision: revision, workflow: draft.workflow }); setValidation(result); if (!result.valid) { setDebug(true); setSelected(result.issues.find(issue => issue.node_id)?.node_id ?? selected); } if (result.valid) setNotice("Граф прошёл проверку"); })}><Check size={15} /> Проверить</Button><Button size="sm" variant="ghost" disabled={!!busy || !capabilities.enabled} onClick={() => void work("Сохраняю", async () => { await save(); })}><Save size={15} /> Сохранить</Button><Button size="sm" disabled={!!busy || !capabilities.enabled} onClick={() => void work("Публикую", async () => { const latest = await save(); await workflowRequest(`scenarios/${draft.scenario_id}/publish`, "POST", { workspace_id: workspace, expected_revision: latest.revision }); await client.invalidateQueries({ queryKey: ["scenario-versions", draft.scenario_id] }); setNotice("Опубликована новая неизменяемая версия"); })}>Опубликовать</Button></div>
    </header>
    {(error || notice || busy || !capabilities.enabled) && <div role={error ? "alert" : "status"} className={`flex items-center justify-between border-b border-border-strong px-5 py-2 text-xs ${error ? "text-danger" : "text-cyan"}`}><span>{error || busy || notice || "Новые workflow-запуски отключены настройкой сервера"}</span>{error.includes("Конфликт") && <Button size="sm" variant="ghost" onClick={() => void work("Загружаю", async () => { const remote = await workflowRequest<Draft>(`scenarios/${draft.scenario_id}/draft?workspace_id=${encodeURIComponent(workspace)}`); commit(remote); setRevision(remote.revision); setSaved(JSON.stringify(remote)); })}>Загрузить актуальный</Button>}</div>}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-strong bg-surface px-3 py-2">
          <div className="flex items-center gap-2"><Button size="sm" variant="ghost" aria-label={paletteOpen ? "Скрыть библиотеку шагов" : "Показать библиотеку шагов"} aria-expanded={paletteOpen} onClick={() => setPaletteOpen(value => !value)}>{paletteOpen ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}</Button><span className="text-xs text-muted">{draft.workflow.nodes.length} шагов<span className="hidden lg:inline"> · {draft.workflow.edges.length} связей</span></span></div>
          <div className="flex items-center gap-2"><span className="hidden items-center gap-3 text-[10px] text-muted xl:flex"><span>▯ Порядок</span><span className="text-cyan">▪ Вход</span><span className="text-purple">▪ Выход</span></span><Button size="sm" variant="ghost" aria-label={settingsOpen ? "Скрыть настройки шага" : "Показать настройки шага"} aria-expanded={settingsOpen} onClick={() => setSettingsOpen(value => !value)}>{settingsOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}</Button></div>
        </div>
    <div className="relative flex min-h-0 flex-1">
      {paletteOpen && <aside aria-label="Библиотека шагов" className="w-48 shrink-0 space-y-5 overflow-auto border-r border-border-strong bg-surface p-3 xl:w-56 xl:p-4">
        <div><p className="mb-3 text-[10px] font-semibold uppercase tracking-widest text-muted">Добавить шаг</p><p className="mb-3 text-[11px] leading-4 text-muted">Новый шаг вставится после выбранного. Для ветвления соедините порты вручную.</p><div className="space-y-1">{(Object.keys(nodeLabels) as NodeType[]).map(type => { const Icon = nodeIcons[type]; return <button key={type} disabled={((type === "input" || type === "result") && draft.workflow.nodes.some(node => node.type === type)) || draft.workflow.nodes.length >= 100} onClick={() => addNode(type)} className="group flex w-full items-center gap-3 rounded-xl border border-transparent px-2 py-2.5 text-left transition-colors hover:border-border-strong hover:bg-elevated/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan disabled:cursor-not-allowed disabled:opacity-30"><span className="rounded-lg bg-elevated/80 p-2" style={{ color: nodeColors[type] }}><Icon size={15} /></span><span><span className="block text-xs font-medium">{nodeLabels[type]}</span><span className="mt-1 block text-[10px] leading-4 text-muted">{nodeDescriptions[type]}</span></span></button>; })}</div></div>
        <details className="border-t border-border-strong pt-4 text-xs text-muted"><summary className="cursor-pointer">Готовые цепочки</summary><p className="my-3 text-[11px] leading-4">Выбор заменит текущий граф. Изменение можно отменить.</p><select aria-label="Заменить граф шаблоном" className={selectClass} value="" onChange={event => { commit({ ...draft, ...templateGraph(event.target.value as Template, capabilities.database_connectors[0]?.name ?? "", capabilities.agents[0]?.id ?? "") }); setSelected("input"); setValidation(null); }}><option value="">Выберите шаблон…</option>{Object.entries(templates).map(([id, item]) => <option key={id} value={id}>{item.title}</option>)}</select></details>
        <div className="border-t border-border-strong pt-4"><p className="mb-3 text-[10px] uppercase tracking-widest text-muted">Версии</p><select aria-label="Загрузить версию в черновик" className={selectClass} value="" disabled={versionQuery.isPending} onChange={event => { const version = versionQuery.data?.find(item => item.id === event.target.value); if (version) commit({ ...draft, workflow: version.workflow }); }}><option value="">{versionQuery.isPending ? "Загружаю версии…" : versionQuery.isError ? "Не удалось загрузить версии" : "Открыть как черновик…"}</option>{versionQuery.data?.map(version => <option key={version.id} value={version.id}>v{version.version} · {version.kind}</option>)}</select>{versionQuery.isError && <p className="mt-2 text-[10px] text-danger">{versionQuery.error.message}</p>}<p className="mt-2 text-[10px] leading-4 text-muted">Публикация фиксирует новую версию. Предыдущие запуски сохраняют своё определение.</p></div>
        <fieldset className="space-y-2 border-t border-border-strong pt-4"><legend className="text-[10px] uppercase tracking-widest text-muted">Политика инструментов</legend>{capabilities.tools.map(tool => <label key={tool.name} className="flex gap-2 text-[11px]"><input type="checkbox" checked={draft.workflow.allowed_tools.includes(tool.name)} onChange={event => commit({ ...draft, workflow: { ...draft.workflow, allowed_tools: event.target.checked ? [...draft.workflow.allowed_tools, tool.name] : draft.workflow.allowed_tools.filter(name => name !== tool.name) } })} />{tool.name}</label>)}</fieldset>
      </aside>}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="relative min-h-[250px] flex-1"><WorkflowCanvas graph={draft.workflow} layout={draft.layout} selected={selected} validation={validation} onSelect={setSelected} onConnect={connect} onPositions={moveNodes} onViewport={moveViewport} onDelete={deleteNodes} /><div className="pointer-events-none absolute bottom-3 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded-lg border border-border-strong bg-surface/90 px-3 py-2 text-[10px] text-muted xl:block">Выход → вход другого шага · Ctrl / ⌘ Z — отмена</div></div>
        <div className="border-t border-border-strong bg-surface"><button aria-expanded={debug} className="flex w-full items-center justify-between px-5 py-3 text-xs hover:bg-elevated/50" onClick={() => setDebug(value => !value)}><span className="flex items-center gap-2"><GitBranch size={14} /> Тестирование и выполнение</span><span>{debug ? "−" : "+"}</span></button>{debug && <div className="max-h-[43vh] space-y-4 overflow-auto border-t border-border-strong p-5">
          <div className="flex flex-wrap items-center gap-2"><label className="mr-3 flex items-center gap-2 text-xs"><input type="checkbox" checked={testNode} onChange={event => { setTestNode(event.target.checked); setTestInputs({}); setTestKey(value => value + 1); }} />Только выбранный шаг{testNode ? `: ${selected}` : ""}</label><Button size="sm" disabled={!!busy || !capabilities.enabled || (testNode && !currentNode)} onClick={() => void run(false)}><Play size={13} /> Тест черновика</Button><Button size="sm" variant="ghost" disabled={!!busy || !capabilities.enabled || !versionQuery.data?.length} onClick={() => void run(true)}>Запустить опубликованную версию</Button><select aria-label="Сохранённые тесты" className={`${selectClass} !w-48`} value="" onChange={event => { const item = draft.test_cases.find(item => item.id === event.target.value); if (item) { setTestInputs(item.inputs); setExpected(item.expected_schema ?? null); setTestKey(value => value + 1); } }}><option value="">Сохранённые тесты…</option>{draft.test_cases.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select><Button size="sm" variant="ghost" onClick={() => commit({ ...draft, test_cases: [...draft.test_cases, { id: crypto.randomUUID(), title: `Тест ${draft.test_cases.length + 1}`, inputs: testInputs, expected_schema: expected }] })}>Сохранить тест</Button></div>
          <div className="grid gap-4 lg:grid-cols-2"><div className="space-y-3"><p className="text-sm font-medium">Тестовые входы</p>{!parameterNames.length && <p className="text-xs text-muted">У сценария нет входных параметров.</p>}{Object.entries(parameterSpecs).map(([key, spec]) => ["file", "files"].includes(spec.type) ? <ScenarioFileParameter key={`${testKey}-${key}`} id={`test-parameter-${key}`} label={"title" in spec && spec.title || key} multiple={spec.type === "files"} required={spec.required !== false} value={testInputs[key] ?? spec.default} disabled={!!busy} uploading={testFileBusy === key} onSelect={files => void uploadTestFiles(key, files)} onRemove={file => void removeTestFile(key, file)} /> : <ValueField key={`${testKey}-${key}`} type={spec.type} label={key} value={testInputs[key] ?? spec.default} onChange={value => setTestInputs(current => ({ ...current, [key]: value }))} />)}<details className="text-xs text-muted"><summary className="cursor-pointer">Редактировать весь JSON</summary><JsonField key={`inputs-${testKey}`} label="Тестовые входы JSON" value={testInputs} onChange={value => { if (value && typeof value === "object" && !Array.isArray(value)) setTestInputs(value as Record<string, unknown>); }} /></details></div><JsonField key={`expected-${testKey}`} label="Ожидаемая JSON Schema результата (null — без проверки)" value={expected} onChange={value => setExpected(value as Record<string, unknown> | null)} rows={4} /></div>
          {validation && <div className="space-y-1 text-xs">{validation.issues.map((issue, index) => <button key={index} onClick={() => issue.node_id && setSelected(issue.node_id)} className="block text-left text-danger">{issue.node_id ? `${issue.node_id}: ` : ""}{issue.message}</button>)}</div>}
          {runId && <WorkflowRunView runId={runId} workspace={workspace} compact />}
        </div>}</div>
      </div>
      <aside aria-label="Настройки шага" className={`${settingsOpen ? "" : "hidden"} w-[300px] shrink-0 overflow-auto border-l border-border-strong bg-surface p-4 xl:w-[360px] xl:p-5`}>{currentNode ? <><div className="mb-4 flex items-center justify-between border-b border-border-strong pb-4"><p className="text-xs font-semibold text-muted">Настройки шага</p><Button size="sm" variant="ghost" disabled={["input", "result"].includes(currentNode.type)} onClick={copyNode}><Copy size={13} /> Копировать</Button></div><WorkflowNodeSettings key={currentNode.id} node={currentNode} graph={draft.workflow} capabilities={capabilities} update={updateNode} updateParameters={parameters => commit({ ...draft, workflow: { ...draft.workflow, parameters } })} /></> : <p className="text-sm text-muted">Выберите шаг на графе</p>}</aside>
    </div>
    {aiOpen && <div className="fixed inset-0 z-50 flex items-center justify-center bg-overlay/70 p-6"><section role="dialog" aria-modal="true" aria-label="AI-помощник сценария" className="max-h-[90vh] w-full max-w-5xl space-y-4 overflow-auto rounded-2xl border border-border-strong bg-surface p-6"><div className="flex justify-between"><h2 className="flex items-center gap-2 font-semibold"><Sparkles size={18} /> AI-помощник</h2><Button size="sm" variant="ghost" onClick={() => setAiOpen(false)}>Закрыть</Button></div><p className="text-xs text-muted">Опишите сценарий или изменение. Предложение проходит проверку и применяется только в черновик.</p><select aria-label="Агент AI-помощника" className={selectClass} value={aiAgent} onChange={event => setAiAgent(event.target.value)}><option value="">Выберите агента…</option>{capabilities.agents.map(agent => <option key={agent.id} value={agent.id}>{agent.title}</option>)}</select><Textarea aria-label="Запрос AI-помощнику" rows={3} placeholder="Например: после SQL добавь Python для группировки выручки по месяцам, затем попроси агента объяснить тренд" value={aiText} onChange={event => setAiText(event.target.value)} /><Button disabled={!aiAgent || !aiText.trim() || !!busy} onClick={() => void work("AI готовит предложение", async () => { setProposal(null); setAiProgress(null); setProposalBase(JSON.stringify(draft.workflow)); const latest = await save(); const controller = new AbortController(); aiRequest.current = controller; try { const response = await requestWorkflowProposal(draft.scenario_id, { workspace_id: workspace, agent_id: aiAgent, instruction: aiText, expected_revision: latest.revision }, setAiProgress, controller.signal); setProposal(response); } finally { aiRequest.current = null; } })}>{busy ? "Запрос отправлен…" : "Предложить изменения"}</Button>{busy === "AI готовит предложение" && <div className="space-y-3"><WorkflowProgress label={aiProgress?.message ?? busy} startedAt={busySince} /><Button size="sm" variant="ghost" onClick={() => aiRequest.current?.abort()}>Остановить</Button></div>}{error && <p role="alert" className="text-sm text-danger">{error}</p>}{proposal && <><p className="rounded-xl border border-success-border bg-success-subtle p-4 text-sm text-success">{proposal.summary}</p><details><summary className="mb-3 cursor-pointer text-sm text-secondary">Посмотреть изменения в графе</summary><DiffEditor height="400px" language="json" original={JSON.stringify(draft.workflow, null, 2)} modified={JSON.stringify(proposal.workflow, null, 2)} options={{ readOnly: true, minimap: { enabled: false }, renderSideBySide: true }} /></details>{proposal.validation.issues.map((issue, index) => <p className="text-xs text-danger" key={index}>{issue.node_id}: {issue.message}</p>)}{proposalBase !== JSON.stringify(draft.workflow) && <p className="text-xs text-warning">Черновик изменился. Запросите новое предложение для актуального графа.</p>}<Button disabled={!!busy || !proposal.validation.valid || proposal.base_revision !== revision || proposalBase !== JSON.stringify(draft.workflow)} onClick={() => { commit({ ...draft, workflow: proposal.workflow }); setProposal(null); setAiOpen(false); setNotice("Предложение применено к черновику. Сохраните и протестируйте изменения"); }}>Применить в черновик</Button></>}</section></div>}
  </main>;
}
