"use client";
import { DropdownSelect } from "@rhetorica/ui";
import { useQuery } from "@tanstack/react-query";
import { type WorkflowNode, type Capabilities } from "@/features/scenarios/workflowApi";
import { codingRequest, missingCodingSettings, type CodingResources, type CodingSelection } from "./codingApi";
import { CodingResourceFields } from "./CodingResourceFields";

export function ScenarioCodingSettings({ node, capabilities, update }: { node: WorkflowNode; capabilities: Capabilities; update: (node: WorkflowNode) => void }) {
  const query = useQuery({ queryKey: ["coding-resources", node.root_agent_id], queryFn: () => codingRequest<CodingResources>(`coding-capabilities?root_agent_id=${encodeURIComponent(node.root_agent_id ?? "")}`), enabled: Boolean(node.root_agent_id) });
  const value: CodingSelection = { dbt_project_subdir: node.dbt_project_subdir ?? ".", root_agent_id: node.root_agent_id, developer_agent_id: node.developer_agent_id ?? "", qa_agent_id: node.qa_agent_id ?? "", gitlab_connector_id: node.gitlab_connector_id ?? "", project_id: node.project_id ?? "", target_branch: node.target_branch ?? "", environment_profile_id: node.environment_profile_id ?? "", database_connector_id: node.database_connector_id };
  const missing = [...(!node.root_agent_id ? ["Координатор"] : []), ...missingCodingSettings(value, query.data), ...(!value.database_connector_id ? ["DWH для dbt-проверок"] : [])];
  return <div className="space-y-3"><DropdownSelect label="Координатор" aria-label="Координатор разработки" value={node.root_agent_id ?? ""} onValueChange={selected => update({ ...node, root_agent_id: selected, developer_agent_id: "", qa_agent_id: "" })} options={[{ value: "", label: "Выберите…" }, ...capabilities.agents.map(a => ({ value: a.id, label: a.title }))]} />
    {query.data && <CodingResourceFields legacy value={value} resources={query.data} onChange={selection => update({ ...node, ...selection, developer_agent_id: selection.developer_agent_id ?? "", qa_agent_id: selection.qa_agent_id ?? "", root_agent_id: node.root_agent_id })} />}
    {query.error && <p role="alert">{query.error.message}</p>}
    {missing.length > 0 && <p className="text-xs text-warning">Не настроено: {missing.join(", ")}</p>}
    <p className="text-xs text-muted">Шаблон не выдаёт права. Администратор настраивает вызов специалистов и delegation grants; без write grant будет подготовлен код без MR</p>
  </div>;
}
