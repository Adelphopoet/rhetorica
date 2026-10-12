"use client";

import * as React from "react";

import { Alert, Badge, Button, Input, Sheet, Switch } from "@rhetorica/ui";
import {
  Activity,
  BookText,
  Check,
  Database,
  GitBranch,
  Server,
  Workflow,
} from "lucide-react";

import type {
  AirflowConnectorItem,
  DatabaseConnectorItem,
  DocumentationConnectorItem,
  GitLabConnectorItem,
  KubernetesConnectorItem,
  LlmConnectorItem,
  McpConnectorItem,
  VictoriaMetricsConnectorItem,
  WorkspaceSkillItem,
} from "./types";
import type { AgentProfile } from "@/features/agents/agentsApi";
import { useSessionState } from "./useSheetSession";

type AgentSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editingAgent: AgentProfile | null;
  databaseConnectors: DatabaseConnectorItem[];
  llmConnectors: LlmConnectorItem[];
  documentationConnectors: DocumentationConnectorItem[];
  gitlabConnectors: GitLabConnectorItem[];
  mcpConnectors: McpConnectorItem[];
  airflowConnectors: AirflowConnectorItem[];
  kubernetesConnectors: KubernetesConnectorItem[];
  victoriametricsConnectors: VictoriaMetricsConnectorItem[];
  workspaceSkills: WorkspaceSkillItem[];
  onSubmit: (formData: FormData) => Promise<void>;
  saving: boolean;
  error: string | null;
  sessionNonce?: number;
};

function Field({
  label,
  children,
  fullWidth = false,
}: {
  label: string;
  children: React.ReactNode;
  fullWidth?: boolean;
}) {
  return (
    <label className={fullWidth ? "md:col-span-2" : undefined}>
      <span className="mb-2 block text-sm font-medium text-primary">{label}</span>
      {children}
    </label>
  );
}

function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className="min-h-36 w-full resize-y rounded-lg border border-control-border bg-surface px-3 py-2 text-sm leading-6 text-primary outline-none transition placeholder:text-muted focus:border-transparent focus:ring-2 focus:ring-accent"
    />
  );
}

function connectorNames<T extends { name: string; is_active: boolean }>(items: T[]) {
  return items
    .filter((item) => item.is_active)
    .map((item) => item.name)
    .sort((left, right) => left.localeCompare(right));
}

export function AgentSheet({
  open,
  onOpenChange,
  editingAgent,
  databaseConnectors,
  llmConnectors,
  documentationConnectors,
  gitlabConnectors,
  mcpConnectors,
  airflowConnectors,
  kubernetesConnectors,
  victoriametricsConnectors,
  workspaceSkills,
  onSubmit,
  saving,
  error,
  sessionNonce = 0,
}: AgentSheetProps) {
  const isEdit = editingAgent != null;
  const databaseOptions = React.useMemo(() => connectorNames(databaseConnectors), [databaseConnectors]);
  const documentationOptions = React.useMemo(
    () => connectorNames(documentationConnectors),
    [documentationConnectors]
  );
  const gitlabOptions = React.useMemo(() => connectorNames(gitlabConnectors), [gitlabConnectors]);
  const mcpOptions = React.useMemo(() => connectorNames(mcpConnectors), [mcpConnectors]);
  const airflowOptions = React.useMemo(() => connectorNames(airflowConnectors), [airflowConnectors]);
  const kubernetesOptions = React.useMemo(() => connectorNames(kubernetesConnectors), [kubernetesConnectors]);
  const victoriametricsOptions = React.useMemo(
    () => connectorNames(victoriametricsConnectors),
    [victoriametricsConnectors]
  );
  const llmOptions = React.useMemo(() => connectorNames(llmConnectors), [llmConnectors]);
  const chatSkillOptions = React.useMemo(
    () =>
      workspaceSkills
        .filter(
          (skill) =>
            skill.capability_family === "chat_instruction" &&
            skill.status === "active" &&
            skill.validation_status === "valid"
        )
        .sort((left, right) => right.priority - left.priority || left.name.localeCompare(right.name)),
    [workspaceSkills]
  );
  const sessionKey = `${editingAgent?.id ?? "new-agent"}:${sessionNonce}`;
  const [llmConnectorName, setLlmConnectorName] = useSessionState(sessionKey, () =>
    editingAgent?.llm_connector_name ?? ""
  );
  const [databaseSelected, setDatabaseSelected] = useSessionState(
    sessionKey,
    () => editingAgent?.allowed_database_connector_names ?? []
  );
  const [documentationSelected, setDocumentationSelected] = useSessionState(
    sessionKey,
    () => editingAgent?.allowed_documentation_connector_names ?? []
  );
  const [gitlabSelected, setGitLabSelected] = useSessionState(
    sessionKey,
    () => editingAgent?.allowed_gitlab_connector_names ?? []
  );
  const [mcpSelected, setMcpSelected] = useSessionState(
    sessionKey,
    () => editingAgent?.allowed_mcp_connector_names ?? []
  );
  const [airflowSelected, setAirflowSelected] = useSessionState(
    sessionKey,
    () => editingAgent?.allowed_airflow_connector_names ?? []
  );
  const [kubernetesSelected, setKubernetesSelected] = useSessionState(
    sessionKey,
    () => editingAgent?.allowed_kubernetes_connector_names ?? []
  );
  const [victoriametricsSelected, setVictoriaMetricsSelected] = useSessionState(
    sessionKey,
    () => editingAgent?.allowed_victoriametrics_connector_names ?? []
  );
  const [workspaceSkillSelected, setWorkspaceSkillSelected] = useSessionState(
    sessionKey,
    () => editingAgent?.allowed_workspace_skill_ids ?? []
  );

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={isEdit ? `Редактирование: ${editingAgent.title}` : "Добавление агента"}
      description="Профиль чата, доступный разным пользователям, со своим промптом, выбором LLM и списками разрешённых подключений"
      keepMounted
    >
      <form
        key={sessionKey}
        onSubmit={(event) => {
          event.preventDefault();
          void onSubmit(new FormData(event.currentTarget));
        }}
        className="space-y-5"
      >
        <input type="hidden" name="agent_id" value={editingAgent?.id ?? ""} />
        <HiddenValues name="database_connectors" values={databaseSelected} />
        <HiddenValues name="documentation_connectors" values={documentationSelected} />
        <HiddenValues name="gitlab_connectors" values={gitlabSelected} />
        <HiddenValues name="mcp_connectors" values={mcpSelected} />
        <HiddenValues name="airflow_connectors" values={airflowSelected} />
        <HiddenValues name="kubernetes_connectors" values={kubernetesSelected} />
        <HiddenValues name="victoriametrics_connectors" values={victoriametricsSelected} />
        <HiddenValues name="workspace_skill_ids" values={workspaceSkillSelected} />
        <input type="hidden" name="llm_connector_name" value={llmConnectorName} />

        {error ? <Alert variant="danger" title="Не удалось сохранить агента" description={error} /> : null}

        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Идентификатор">
            <Input data-autofocus name="slug" defaultValue={editingAgent?.slug ?? ""} required />
          </Field>
          <Field label="Название">
            <Input name="title" defaultValue={editingAgent?.title ?? ""} required />
          </Field>
          <Field label="Описание" fullWidth>
            <Input name="description" defaultValue={editingAgent?.description ?? ""} />
          </Field>
        </div>

        <div className="rounded-[18px] border border-border-strong bg-surface p-4">
          <p className="mb-3 text-sm font-medium text-primary">Подключение LLM</p>
          <SingleChoiceChips
            value={llmConnectorName}
            options={["", ...llmOptions]}
            emptyLabel="По умолчанию"
            onChange={setLlmConnectorName}
          />
        </div>

        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-[18px] border border-border-strong bg-surface px-4 py-3">
            <Switch name="is_active" defaultChecked={editingAgent?.is_active ?? true} label="Активен" />
          </div>
          <div className="rounded-[18px] border border-border-strong bg-surface px-4 py-3">
            <Switch
              name="run_sql_allowed"
              defaultChecked={editingAgent?.settings.run_sql_allowed ?? true}
              label="SQL"
            />
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Температура">
            <Input
              name="temperature"
              type="number"
              step="0.1"
              min="0"
              max="2"
              defaultValue={editingAgent?.settings.temperature ?? ""}
            />
          </Field>
          <Field label="Максимум токенов">
            <Input
              name="max_tokens"
              type="number"
              min="1"
              max="32000"
              defaultValue={editingAgent?.settings.max_tokens ?? ""}
            />
          </Field>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <ConnectorAllowlist
            title="База данных"
            icon={<Database className="h-4 w-4" />}
            options={databaseOptions}
            values={databaseSelected}
            onChange={setDatabaseSelected}
          />
          <ConnectorAllowlist
            title="Документация"
            icon={<BookText className="h-4 w-4" />}
            options={documentationOptions}
            values={documentationSelected}
            onChange={setDocumentationSelected}
          />
          <ConnectorAllowlist
            title="GitLab"
            icon={<GitBranch className="h-4 w-4" />}
            options={gitlabOptions}
            values={gitlabSelected}
            onChange={setGitLabSelected}
          />
          <ConnectorAllowlist
            title="MCP"
            icon={<Server className="h-4 w-4" />}
            options={mcpOptions}
            values={mcpSelected}
            onChange={setMcpSelected}
          />
          <ConnectorAllowlist
            title="Airflow"
            icon={<Workflow className="h-4 w-4" />}
            options={airflowOptions}
            values={airflowSelected}
            onChange={setAirflowSelected}
          />
          <ConnectorAllowlist
            title="Kubernetes"
            icon={<Server className="h-4 w-4" />}
            options={kubernetesOptions}
            values={kubernetesSelected}
            onChange={setKubernetesSelected}
          />
          <ConnectorAllowlist
            title="VictoriaMetrics"
            icon={<Activity className="h-4 w-4" />}
            options={victoriametricsOptions}
            values={victoriametricsSelected}
            onChange={setVictoriaMetricsSelected}
          />
        </div>

        <WorkspaceSkillAllowlist
          skills={chatSkillOptions}
          values={workspaceSkillSelected}
          onChange={setWorkspaceSkillSelected}
        />

        <Field label="Системный промпт" fullWidth>
          <TextArea name="admin_prompt" defaultValue={editingAgent?.admin_prompt ?? ""} />
        </Field>

        <div className="rounded-[24px] border border-border bg-surface/90 p-4">
          <div className="flex items-center gap-2">
            <Badge variant="info">{isEdit ? "Обновление" : "Создание"}</Badge>
            <p className="text-sm text-primary">
              Пустой список разрешений означает запрет для этого типа подключений.
            </p>
          </div>
        </div>

        <div className="flex items-center justify-end gap-3">
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            className="border border-border-strong bg-surface text-primary hover:bg-elevated"
          >
            Отмена
          </Button>
          <Button type="submit" isLoading={saving}>
            {isEdit ? "Сохранить агента" : "Создать агента"}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}

function HiddenValues({ name, values }: { name: string; values: string[] }) {
  return (
    <>
      {values.map((value) => (
        <input key={value} type="hidden" name={name} value={value} />
      ))}
    </>
  );
}

function SingleChoiceChips({
  value,
  options,
  emptyLabel,
  onChange,
}: {
  value: string;
  options: string[];
  emptyLabel: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((option) => {
        const selected = option === value;
        return (
          <button
            key={option || "__default"}
            type="button"
            onClick={() => onChange(option)}
            className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm transition ${
              selected
                ? "border-accent/50 bg-accent/[0.18] text-primary"
                : "border-border-strong bg-surface text-secondary hover:border-border-strong hover:text-primary"
            }`}
          >
            <Server className="h-4 w-4" />
            {option || emptyLabel}
          </button>
        );
      })}
    </div>
  );
}

function ConnectorAllowlist({
  title,
  icon,
  options,
  values,
  onChange,
}: {
  title: string;
  icon: React.ReactNode;
  options: string[];
  values: string[];
  onChange: (values: string[]) => void;
}) {
  const selected = new Set(values);

  function toggle(option: string) {
    if (selected.has(option)) {
      onChange(values.filter((value) => value !== option));
      return;
    }
    onChange([...values, option]);
  }

  return (
    <div className="rounded-[18px] border border-border-strong bg-surface p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium text-primary">
          {icon}
          {title}
        </div>
        <span className="text-xs text-secondary">{values.length}/{options.length}</span>
      </div>
      {options.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {options.map((option) => {
            const checked = selected.has(option);
            return (
              <button
                key={option}
                type="button"
                onClick={() => toggle(option)}
                className={`inline-flex min-h-9 items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition ${
                  checked
                    ? "border-accent/50 bg-accent/[0.18] text-primary"
                    : "border-border-strong bg-surface text-secondary hover:border-border-strong hover:text-primary"
                }`}
              >
                {checked ? <Check className="h-3.5 w-3.5" /> : null}
                {option}
              </button>
            );
          })}
        </div>
      ) : (
        <p className="text-sm text-secondary">Нет активных подключений</p>
      )}
    </div>
  );
}

function WorkspaceSkillAllowlist({
  skills,
  values,
  onChange,
}: {
  skills: WorkspaceSkillItem[];
  values: string[];
  onChange: (values: string[]) => void;
}) {
  const selected = new Set(values);

  function toggle(skillId: string) {
    if (selected.has(skillId)) {
      onChange(values.filter((value) => value !== skillId));
      return;
    }
    onChange([...values, skillId]);
  }

  return (
    <div className="rounded-[18px] border border-border-strong bg-surface p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium text-primary">
          <BookText className="h-4 w-4" />
          Навыки для чата
        </div>
        <span className="text-xs text-secondary">{values.length}/{skills.length}</span>
      </div>
      {skills.length > 0 ? (
        <div
          role="group"
          aria-label="Доступные навыки для чата"
          className="grid max-h-[min(55vh,36rem)] gap-2 overflow-y-auto overscroll-contain pr-1 [scrollbar-gutter:stable]"
        >
          {skills.map((skill) => {
            const checked = selected.has(skill.id);
            return (
              <button
                key={skill.id}
                type="button"
                onClick={() => toggle(skill.id)}
                className={`min-h-12 rounded-lg border px-3 py-2 text-left text-sm transition ${
                  checked
                    ? "border-accent/50 bg-accent/[0.18] text-primary"
                    : "border-border-strong bg-surface text-secondary hover:border-border-strong hover:text-primary"
                }`}
              >
                <span className="flex items-center gap-2">
                  {checked ? <Check className="h-3.5 w-3.5 shrink-0" /> : null}
                  <span className="font-medium">{skill.title || skill.name}</span>
                </span>
                {skill.summary ? (
                  <span className="mt-1 block text-xs text-secondary">{skill.summary}</span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : (
        <p className="text-sm text-secondary">Нет активных корректных навыков для чата</p>
      )}
    </div>
  );
}
