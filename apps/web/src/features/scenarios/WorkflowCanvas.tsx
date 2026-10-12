"use client";

import { ArrowRight } from "lucide-react";
import { useTheme } from "@/features/theme/ThemeProvider";
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { applyNodeChanges, Background, Controls, Handle, MiniMap, Position, ReactFlow, useNodesState, useEdgesState, useUpdateNodeInternals, type Connection, type Edge, type Node, type NodeChange, type NodeProps, type Viewport } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./workflowCanvas.css";
import { nodeColors as colors, nodeIcons } from "./workflowNodePresentation";
import { connectWorkflow, workflowEdges } from "./workflowConnections";
import { inputHandle, outputHandle, outputPorts } from "./workflowPorts";
import { valueLabels } from "./workflowAuthoring";
import { nodeLabels, type ValueSpec, type Draft, type Validation, type WorkflowDefinition, type WorkflowNode } from "./workflowApi";

const handleClass = "workflow-handle !h-3 !w-3 !rounded-[4px] !border-2 !border-elevated ring-1 ring-muted/50 transition-shadow hover:ring-[3px] hover:ring-cyan [&.connectingfrom]:ring-[3px] [&.connectingto]:ring-[3px] [&.valid]:ring-success";
type FlowNode = Node<{ step: WorkflowNode; invalid: boolean; parameters?: Record<string, ValueSpec> }, "scenario">;

export const ScenarioGraphNode = memo(function ScenarioGraphNode({ id, data, selected }: NodeProps<FlowNode>) {
  const node = data.step;
  const Icon = nodeIcons[node.type];
  const updateInternals = useUpdateNodeInternals();
  const ports = outputPorts(node, data.parameters);
  useEffect(() => { updateInternals(id); }, [id, node.inputs, node.outputs, data.parameters, updateInternals]);
  return <div className={`w-64 rounded-2xl border bg-elevated shadow-[0_6px_24px_rgb(var(--color-shadow)/calc(0.2*var(--shadow-opacity-scale)))] transition-shadow ${selected ? "ring-2 ring-cyan/40" : ""}`} style={{ borderColor: data.invalid ? "rgb(var(--color-danger))" : selected ? colors[node.type] : "rgb(var(--color-border-strong))" }}>
    <div className="relative flex items-center justify-between border-b border-border-strong/60 px-4 py-3 text-[10px] uppercase tracking-wider" style={{ color: colors[node.type] }}>
      {node.type !== "input" && <Handle type="target" position={Position.Left} title="Порядок выполнения: предыдущий шаг" aria-label={`Порядок выполнения: вход ${node.title}`} className={`${handleClass} workflow-order-handle !bg-muted`} />}
      <span className="flex items-center gap-2 font-semibold"><Icon size={14} aria-hidden="true" />{nodeLabels[node.type]}</span><ArrowRight size={12} aria-hidden="true" className="text-muted" />
      {!["condition", "result"].includes(node.type) && <Handle type="source" position={Position.Right} title="Порядок выполнения: следующий шаг" aria-label={`Порядок выполнения: выход ${node.title}`} className={`${handleClass} workflow-order-handle !bg-muted`} />}
    </div>
    <div className="px-4 py-3"><strong title={node.title} className="block truncate text-sm text-primary">{node.title}</strong>
      <div className="mt-3 grid grid-cols-2 gap-4">
        <div className="min-w-0 space-y-1">{node.type !== "input" && <><p className="mb-2 text-[9px] uppercase tracking-wider text-muted">Входы</p>{Object.entries(node.inputs).map(([name, binding]) => <div key={name} className="relative py-1" title={`${name} · ${valueLabels[binding.type]}`}><Handle type="target" id={inputHandle(name)} position={Position.Left} style={{ left: -16 }} className={`${handleClass} !bg-cyan`} aria-label={`Вход ${node.title}: ${name}`} /><span className="block truncate text-[11px] text-cyan">{name}</span><span className="text-[9px] text-muted">{valueLabels[binding.type]}</span></div>)}<div className="relative py-2"><Handle type="target" id="add-input" position={Position.Left} style={{ left: -16 }} className={`${handleClass} !bg-cyan`} aria-label={`Добавить вход ${node.title}`} title="Перетащите сюда выход другого шага" /><span className="text-[11px] text-cyan">+ Вход</span></div></>}</div>
        <div className="min-w-0 space-y-1 text-right">{node.type !== "result" && <><p className="mb-2 text-[9px] uppercase tracking-wider text-muted">Выходы</p>{node.type === "condition" && (["true", "false"] as const).map(branch => <div key={branch} className="relative py-2"><span className="text-[11px] text-secondary">{branch === "true" ? "Да" : "Нет"}</span><Handle type="source" id={branch} position={Position.Right} style={{ right: -16, background: branch === "true" ? "rgb(var(--color-success))" : "rgb(var(--color-orange))" }} className={handleClass} aria-label={`Ветвь ${branch}`} /></div>)}{ports.map(port => <div key={outputHandle(port.path)} className="relative py-1" title={`${port.label} · ${valueLabels[port.type]}`}><span className="block truncate text-[11px] text-purple">{port.label}</span><span className="text-[9px] text-muted">{valueLabels[port.type]}</span><Handle type="source" id={outputHandle(port.path)} position={Position.Right} style={{ right: -16 }} className={`${handleClass} !bg-purple`} aria-label={`Выход ${node.title}: ${port.label}`} title="Перетащите во вход следующего шага" /></div>)}{!ports.length && <p className="text-[10px] leading-4 text-muted">{node.type === "input" ? "Добавьте параметры справа" : "Настройте результаты шага"}</p>}</>}</div>
      </div>
    </div>
  </div>;
});
const nodeTypes = { scenario: ScenarioGraphNode };

type Props = {
  graph: WorkflowDefinition; layout: Draft["layout"]; selected: string; validation: Validation | null;
  onSelect: (id: string) => void; onConnect: (connection: Connection) => void;
  onPositions: (positions: Draft["layout"]["positions"]) => void; onViewport: (viewport: Viewport) => void;
  onDelete: (deleted: { nodes: Node[]; edges: Edge[] }) => void;
};
const fitOptions = { minZoom: 0.3, maxZoom: 1 };
const proOptions = { hideAttribution: true };
const deleteKeys = ["Backspace", "Delete"];

export const WorkflowCanvas = memo(function WorkflowCanvas({ graph, layout, selected, validation, onSelect, onConnect, onPositions, onViewport, onDelete }: Props) {
  const { resolvedTheme } = useTheme();
  const [initialViewport] = useState(layout.viewport?.zoom ? layout.viewport as Viewport : undefined);
  const incoming = useMemo<FlowNode[]>(() => graph.nodes.map((step, index) => ({ id: step.id, type: "scenario", selected: selected === step.id, position: layout.positions[step.id] ?? { x: 60 + index * 300, y: 130 }, data: { step, parameters: step.type === "input" ? graph.parameters : undefined, invalid: validation?.issues.some(issue => issue.node_id === step.id) ?? false } })), [graph.nodes, graph.parameters, layout.positions, selected, validation]);
  const [nodes, setNodes] = useNodesState<FlowNode>(incoming);
  useEffect(() => {
    setNodes(current => incoming.map(node => {
      const previous = current.find(item => item.id === node.id);
      return previous ? { ...previous, ...node, position: previous.dragging ? previous.position : node.position, data: previous.data.step === node.data.step && previous.data.invalid === node.data.invalid && previous.data.parameters === node.data.parameters ? previous.data : node.data } : node;
    }));
  }, [incoming, setNodes]);
  const incomingEdges = useMemo(() => workflowEdges(graph), [graph]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(incomingEdges);
  useEffect(() => setEdges(current => incomingEdges.map(edge => ({ ...edge, selected: current.find(item => item.id === edge.id && item.source === edge.source && item.target === edge.target)?.selected }))), [incomingEdges, setEdges]);
  const onNodesChange = useCallback((changes: NodeChange<FlowNode>[]) => {
    setNodes(current => applyNodeChanges(changes, current));
    const select = changes.find(change => change.type === "select" && change.selected);
    if (select?.type === "select") onSelect(select.id);
    const positions = changes.flatMap(change => change.type === "position" && change.position && change.dragging === undefined ? [[change.id, change.position]] : []);
    if (positions.length) onPositions(Object.fromEntries(positions));
  }, [setNodes, onSelect, onPositions]);
  const onDragStop = useCallback((_event: unknown, _node: FlowNode, moved: FlowNode[]) => onPositions(Object.fromEntries(moved.map(node => [node.id, node.position]))), [onPositions]);
  const onMoveEnd = useCallback((event: unknown, viewport: Viewport) => { if (event) onViewport(viewport); }, [onViewport]);
  const onNodeClick = useCallback((_event: unknown, node: FlowNode) => onSelect(node.id), [onSelect]);
  const validConnection = useCallback((connection: Connection | Edge) => {
    try { connectWorkflow(graph, { source: connection.source, target: connection.target, sourceHandle: connection.sourceHandle ?? null, targetHandle: connection.targetHandle ?? null }); return true; } catch { return false; }
  }, [graph]);
  return <ReactFlow className="scenario-canvas" nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} onNodeDragStop={onDragStop} onMoveEnd={onMoveEnd} onNodeClick={onNodeClick} onDelete={onDelete} isValidConnection={validConnection} connectionRadius={32} defaultViewport={initialViewport} fitView={!initialViewport} fitViewOptions={fitOptions} colorMode={resolvedTheme} minZoom={0.15} maxZoom={1.8} deleteKeyCode={deleteKeys} proOptions={proOptions} onlyRenderVisibleElements={false}>
    <Background color="rgb(var(--color-border-strong))" gap={24} /><Controls aria-label="Управление масштабом" /><MiniMap nodeColor={node => colors[(node.data as FlowNode["data"]).step.type]} maskColor="rgb(var(--color-page) / .7)" pannable zoomable ariaLabel="Обзор сценария" />
  </ReactFlow>;
});
