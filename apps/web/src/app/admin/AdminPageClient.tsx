"use client";

import { pluralize } from "@/lib/plural";
import { ThemeSelector } from "@/features/theme/ThemeSelector";
import { buildLlmExtraSettings, parseLlmHeaders } from "./llmSettings";
import Link from "next/link";
import {
  startTransition,
  useDeferredValue,
  useEffect,
  useMemo,
  useState,
  useTransition,
} from "react";
import { AnimatePresence, LayoutGroup, motion } from "framer-motion";
import {
  Alert,
  Button,
  buttonVariants,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  EmptyState,
  Input,
  Switch,
  useToast,
} from "@rhetorica/ui";
import {
  BookText,
  CheckCircle2,
  Clock3,
  Database,
  Edit,
  GitBranch,
  MessageSquare,
  Plus,
  Search,
  Server,
  Shield,
  Sparkles,
  Trash2,
  Users,
  Workflow,
  X,
  XCircle,
  Activity,
} from "lucide-react";

import { AgentSheet } from "./AgentSheet";
import { ConnectorSheet } from "./ConnectorSheet";
import { SkillSheet } from "./SkillSheet";
import { UserRateLimitDialog } from "./UserRateLimitDialog";
import { useSessionNonce } from "./useSheetSession";
import {
  buildReadinessModel,
  formatDateTime,
  formatShortDate,
  getConnectorHealth,
  getConnectorTypeLabel,
  matchesConnectorFilter,
  sortScenariosForGovernance,
  sortSkills,
  sortUsers,
  type SetupStatusFilter,
} from "./adminUtils";
import type {
  AdminPageData,
  AdminSectionId,
  AnyConnectorItem,
  AirflowConnectorItem,
  ConnectorType,
  DatabaseExtraSetting,
  DatabaseConnectorItem,
  DocumentationConnectorItem,
  GitLabConnectorItem,
  KubernetesConnectorItem,
  LlmConnectorItem,
  LocalUserItem,
  MattermostConnectorItem,
  McpConnectorItem,
  VictoriaMetricsConnectorItem,
  WorkspaceSkillItem,
} from "./types";
import {
  archiveScenario,
  type ScenarioDefinition,
} from "@/features/scenarios/scenariosApi";
import type { AgentProfile } from "@/features/agents/agentsApi";

type DeleteIntent =
  | { kind: "connector"; id: string; connectorType: ConnectorType; title: string }
  | { kind: "skill"; id: string; title: string }
  | { kind: "user"; user: LocalUserItem }
  | { kind: "agent"; agent: AgentProfile }
  | { kind: "scenario"; scenario: ScenarioDefinition };

type SectionMeta = {
  id: AdminSectionId;
  label: string;
  description: string;
};

const MAX_MANUAL_SKILL_CONTENT_BYTES = 32_768;

const SETUP_STATUS_FILTER_LABELS: Record<SetupStatusFilter, string> = {
  all: "Все",
  ready: "Готовые",
  attention: "Требуют внимания",
};

const SECTION_META: SectionMeta[] = [
  {
    id: "overview",
    label: "Обзор",
    description: "Готовность, блокеры, следующие шаги",
  },
  {
    id: "setup",
    label: "Настройки",
    description: "Подключения и настройка выполнения",
  },
  {
    id: "governance",
    label: "Управление",
    description: "Официальные сценарии и навыки",
  },
  {
    id: "access",
    label: "Доступ",
    description: "Локальные пользователи и права",
  },
  {
    id: "review",
    label: "Проверка",
    description: "Проверка инсайтов RAG",
  },
];

function parseErrorText(text: string, fallbackStatus: number) {
  if (!text) {
    return `Запрос завершился с кодом ${fallbackStatus}`;
  }

  try {
    const payload = JSON.parse(text) as Record<string, unknown>;
    const detail = payload.detail ?? payload.message ?? payload.error ?? payload.body;
    if (typeof detail === "string" && detail.length > 0) {
      return detail;
    }
  } catch {
    return text;
  }

  return `Запрос завершился с кодом ${fallbackStatus}`;
}

async function requestJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    ...init,
    cache: "no-store",
  });

  const text = await response.text();
  if (!response.ok) {
    throw new Error(parseErrorText(text, response.status));
  }

  return text ? (JSON.parse(text) as T) : (undefined as T);
}

function connectorEndpoint(type: ConnectorType, id?: string, workspaceId?: string) {
  const base =
    type === "database"
      ? "/api/admin/connectors"
      : type === "llm"
        ? "/api/admin/llm-connectors"
        : type === "documentation"
              ? "/api/admin/documentation-connectors"
      : type === "gitlab"
        ? "/api/admin/gitlab-connectors"
      : type === "mcp"
        ? "/api/admin/mcp-connectors"
      : type === "airflow"
        ? "/api/admin/airflow-connectors"
        : type === "kubernetes"
          ? "/api/admin/kubernetes-connectors"
          : type === "victoriametrics"
            ? "/api/admin/victoriametrics-connectors"
          : "/api/admin/mattermost-connectors";

  if (!id) {
    return base;
  }

  return `${base}/${id}${workspaceId ? `?workspace_id=${workspaceId}` : ""}`;
}

function createTempId(prefix: string) {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

function normalizeDatabaseEngine(value: FormDataEntryValue | null): DatabaseConnectorItem["connection"]["engine"] {
  const engine = String(value ?? "clickhouse").trim().toLowerCase();
  if (engine === "trino" || engine === "greenplum" || engine === "impala") {
    return engine;
  }
  return "clickhouse";
}

function putStringSetting(
  settings: Record<string, unknown>,
  key: string,
  value: FormDataEntryValue | null
) {
  const parsed = String(value ?? "").trim();
  if (parsed) {
    settings[key] = parsed;
  }
}

function putNumberSetting(
  settings: Record<string, unknown>,
  key: string,
  value: FormDataEntryValue | null
) {
  const parsed = String(value ?? "").trim();
  if (parsed) {
    settings[key] = Number(parsed);
  }
}

function optionalNumber(value: FormDataEntryValue | null): number | null {
  const raw = String(value ?? "").trim();
  if (!raw) {
    return null;
  }
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function optionalText(value: FormDataEntryValue | null): string | null {
  const raw = String(value ?? "").trim();
  return raw ? raw : null;
}

function splitCommaList(value: FormDataEntryValue | null): string[] {
  return String(value ?? "")
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseJsonArrayField(value: FormDataEntryValue | null): unknown[] {
  const raw = String(value ?? "").trim();
  if (!raw) {
    return [];
  }
  const parsed = JSON.parse(raw) as unknown;
  if (!Array.isArray(parsed)) {
    throw new Error("Ожидался массив JSON");
  }
  return parsed;
}

function parseJsonObjectField(value: FormDataEntryValue | null): Record<string, string> {
  const raw = String(value ?? "").trim();
  if (!raw) {
    return {};
  }
  const parsed = JSON.parse(raw) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return {};
  }
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(parsed)) {
    if (typeof value === "string" && key.trim() && value.trim()) {
      result[key.trim()] = value.trim();
    }
  }
  return result;
}

function parseMcpSecretHeaders(value: FormDataEntryValue | null) {
  const raw = String(value ?? "").trim();
  if (!raw) {
    return null;
  }
  return parseJsonArrayField(value)
    .filter((item): item is { name: string; value: string; mode?: string } => {
      return (
        typeof item === "object" &&
        item !== null &&
        typeof (item as { name?: unknown }).name === "string" &&
        typeof (item as { value?: unknown }).value === "string"
      );
    })
    .map((item) => ({
      name: item.name.trim(),
      value: item.value.trim(),
      mode: item.mode === "bearer" ? "bearer" : "raw",
    }))
    .filter((item) => item.name && item.value);
}

function selectedValues(formData: FormData, fieldName: string): string[] {
  return formData.getAll(fieldName).map(String).filter(Boolean);
}

function withManualSkillUseWhen(markdown: string, useWhen: string) {
  const trimmedUseWhen = useWhen.trim();
  if (!trimmedUseWhen) {
    return markdown;
  }
  const escapedUseWhen = JSON.stringify(trimmedUseWhen);
  const lines = markdown.split(/\r?\n/);
  if (lines[0]?.trim() !== "---") {
    return `---\nuse_when: ${escapedUseWhen}\n---\n\n${markdown}`;
  }
  const closingIndex = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (closingIndex < 0) {
    return `---\nuse_when: ${escapedUseWhen}\n---\n\n${markdown}`;
  }
  const frontmatter = lines.slice(1, closingIndex);
  const body = lines.slice(closingIndex);
  const useWhenIndex = frontmatter.findIndex((line) => line.trim().startsWith("use_when:"));
  if (useWhenIndex >= 0) {
    frontmatter[useWhenIndex] = `use_when: ${escapedUseWhen}`;
  } else {
    frontmatter.push(`use_when: ${escapedUseWhen}`);
  }
  return ["---", ...frontmatter, ...body].join("\n");
}

function utf8ByteLength(value: string) {
  return new TextEncoder().encode(value).length;
}

function isDatabaseExtraSetting(value: unknown): value is DatabaseExtraSetting {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return true;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  return Object.values(value).every(
    (item) => typeof item === "string" || typeof item === "number" || typeof item === "boolean"
  );
}

function parseDatabaseAdditionalSettings(
  value: FormDataEntryValue | null
): Record<string, DatabaseExtraSetting> {
  const raw = String(value ?? "").trim();
  if (!raw) {
    return {};
  }
  const parsed = JSON.parse(raw) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Дополнительные настройки должны быть объектом JSON");
  }
  const settings: Record<string, DatabaseExtraSetting> = {};
  for (const [rawKey, settingValue] of Object.entries(parsed)) {
    const key = rawKey.trim();
    if (!key || !isDatabaseExtraSetting(settingValue)) {
      throw new Error(`Недопустимый параметр настройки: ${rawKey || "<empty>"}`);
    }
    settings[key] = settingValue;
  }
  return settings;
}

function buildDatabaseExtraSettings(formData: FormData): Record<string, DatabaseExtraSetting> {
  const settings = parseDatabaseAdditionalSettings(formData.get("additional_settings_json"));
  putNumberSetting(settings, "query_timeout_seconds", formData.get("query_timeout_seconds"));
  if (normalizeDatabaseEngine(formData.get("engine")) === "impala") {
    putStringSetting(settings, "auth_type", formData.get("auth_type"));
    putNumberSetting(settings, "retries", formData.get("retries"));
    putStringSetting(settings, "ca_cert", formData.get("ca_cert"));
    putStringSetting(settings, "http_path", formData.get("http_path"));
    putNumberSetting(settings, "timeout", formData.get("timeout"));
    if (formData.get("use_http_transport") !== null) {
      settings.use_http_transport = true;
    }
  }
  return settings;
}

function collectDatabaseExtraCredentials(
  formData: FormData
): Record<string, string | null> | null {
  if (normalizeDatabaseEngine(formData.get("engine")) !== "trino") {
    return null;
  }
  const names = formData.getAll("extra_credential_name").map((value) => String(value).trim());
  const values = formData.getAll("extra_credential_value").map((value) => String(value));
  const stored = formData.getAll("extra_credential_stored").map((value) => value === "true");
  const credentials: Record<string, string | null> = {};
  names.forEach((name, index) => {
    if (!name) {
      throw new Error("Требуется имя дополнительных учётных данных Trino");
    }
    if (Object.prototype.hasOwnProperty.call(credentials, name)) {
      throw new Error(`Повторяющиеся учётные данные Trino: ${name}`);
    }
    const secret = values[index] ?? "";
    if (!secret && !stored[index]) {
      throw new Error(`Требуется значение для новых учётных данных Trino: ${name}`);
    }
    credentials[name] = secret || null;
  });
  return credentials;
}

function collectLlmApiKeys(formData: FormData) {
  const keys = formData
    .getAll("api_keys")
    .map((value) => String(value).trim())
    .filter(Boolean);
  return keys.length > 0 ? keys : null;
}

function collectRetainedLlmApiKeyIndexes(formData: FormData) {
  const indexes = formData
    .getAll("retained_api_key_indexes")
    .map((value) => Number(String(value)))
    .filter((value) => Number.isInteger(value) && value >= 0);
  return indexes.length > 0 ? indexes : null;
}

function buildConnectorSaveBody(
  connector: AnyConnectorItem,
  type: ConnectorType,
  workspaceId: string,
  isActive: boolean
) {
  if (type === "database") {
    const item = connector as DatabaseConnectorItem;
    return {
      name: item.name,
      workspace_id: workspaceId,
      is_active: isActive,
      connection: {
        ...item.connection,
        password: null,
      },
    };
  }
  if (type === "llm") {
    const item = connector as LlmConnectorItem;
    return {
      name: item.name,
      workspace_id: workspaceId,
      is_active: isActive,
      is_default: item.is_default,
      connection: {
        ...item.connection,
        api_key: null,
        api_keys: null,
        retained_api_key_indexes: null,
        extra_settings: item.connection.extra_settings ?? {},
      },
    };
  }
  if (type === "documentation") {
    const item = connector as DocumentationConnectorItem;
    return {
      name: item.name,
      workspace_id: workspaceId,
      is_active: isActive,
      visible_to_default_assistant: item.visible_to_default_assistant,
      connection: {
        ...item.connection,
        api_token: null,
        embedding_api_key: null,
        qdrant_api_key: null,
        extra_settings: item.connection.extra_settings ?? {},
      },
    };
  }
  if (type === "gitlab") {
    const item = connector as GitLabConnectorItem;
    return {
      name: item.name,
      workspace_id: workspaceId,
      is_active: isActive,
      connection: {
        ...item.connection,
        api_token: null,
        extra_settings: item.connection.extra_settings ?? {},
      },
    };
  }
  if (type === "mcp") {
    const item = connector as McpConnectorItem;
    return {
      name: item.name,
      workspace_id: workspaceId,
      is_active: isActive,
      connection: {
        ...item.connection,
        secret_headers: null,
        extra_settings: item.connection.extra_settings ?? {},
      },
    };
  }
  if (type === "airflow") {
    const item = connector as AirflowConnectorItem;
    return {
      name: item.name,
      workspace_id: workspaceId,
      is_active: isActive,
      connection: {
        ...item.connection,
        password: null,
        extra_settings: item.connection.extra_settings ?? {},
      },
    };
  }
  if (type === "kubernetes") {
    const item = connector as KubernetesConnectorItem;
    return {
      name: item.name,
      workspace_id: workspaceId,
      is_active: isActive,
      visible_to_default_assistant: item.visible_to_default_assistant,
      connection: {
        ...item.connection,
        bearer_token: null,
        extra_settings: item.connection.extra_settings ?? {},
      },
    };
  }
  if (type === "victoriametrics") {
    const item = connector as VictoriaMetricsConnectorItem;
    return {
      name: item.name,
      workspace_id: workspaceId,
      is_active: isActive,
      visible_to_default_assistant: item.visible_to_default_assistant,
      connection: {
        ...item.connection,
        bearer_token: null,
        extra_settings: item.connection.extra_settings ?? {},
      },
    };
  }
  const item = connector as MattermostConnectorItem;
  return {
    name: item.name,
    workspace_id: workspaceId,
    is_active: isActive,
    connection: {
      ...item.connection,
      bot_token: null,
      extra_settings: item.connection.extra_settings ?? {},
    },
  };
}

function SectionBadge({
  tone,
  children,
}: {
  tone: "primary" | "success" | "warning" | "danger";
  children: React.ReactNode;
}) {
  const map = {
    primary: "bg-accent/[0.16] text-accent border-accent/[0.28]",
    success: "bg-success/[0.16] text-success border-success/[0.24]",
    warning: "bg-warning/[0.14] text-warning border-warning/[0.28]",
    danger: "bg-danger/[0.16] text-danger border-danger/[0.24]",
  };

  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${map[tone]}`}>
      {children}
    </span>
  );
}

function SectionShell({
  eyebrow,
  title,
  description,
  actions,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="grid gap-5">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted">
            {eyebrow}
          </p>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-primary">
            {title}
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">{description}</p>
        </div>
        {actions ? <div className="flex flex-wrap gap-3">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

function MetricCard({
  icon,
  title,
  value,
  tone,
  caption,
}: {
  icon: React.ReactNode;
  title: string;
  value: React.ReactNode;
  tone: "primary" | "success" | "warning" | "danger";
  caption: string;
}) {
  const map = {
    primary: "border-accent-border bg-[radial-gradient(circle_at_top_right,rgb(var(--color-accent)/0.16),transparent_34%),linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.98))] text-accent",
    success: "border-success-border bg-[radial-gradient(circle_at_top_right,rgb(var(--color-success)/0.16),transparent_34%),linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.98))] text-success",
    warning: "border-warning-border bg-[radial-gradient(circle_at_top_right,rgb(var(--color-warning)/0.16),transparent_34%),linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.98))] text-warning",
    danger: "border-danger-border bg-[radial-gradient(circle_at_top_right,rgb(var(--color-danger)/0.16),transparent_34%),linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.98))] text-danger",
  };

  return (
    <Card className={`border ${map[tone]} shadow-[0_18px_48px_rgb(var(--color-shadow)/calc(0.28*var(--shadow-opacity-scale)))]`}>
      <CardContent className="flex items-start gap-4 p-5">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-current/20 bg-current/10">
          {icon}
        </div>
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted">{title}</p>
          <div className="text-3xl font-semibold tracking-[-0.04em] text-primary">{value}</div>
          <p className="text-sm leading-6 text-secondary">{caption}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function InfoPanel({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="min-w-0 overflow-hidden border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.96),rgb(var(--color-surface)/0.98))]">
      <CardHeader className="min-w-0 border-b border-border">
        <CardTitle className="min-w-0 break-words text-lg">{title}</CardTitle>
        {description ? <CardDescription className="min-w-0 break-words">{description}</CardDescription> : null}
      </CardHeader>
      <CardContent className="min-w-0 p-5">{children}</CardContent>
    </Card>
  );
}

function ReviewTag({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex rounded-full border border-border-strong bg-surface px-2.5 py-1 text-xs text-primary">
      {children}
    </span>
  );
}

export function AdminPageClient({
  databaseConnectors: initialDb,
  llmConnectors: initialLlm,
  documentationConnectors: initialDocumentation,
  gitlabConnectors: initialGitLab,
  mcpConnectors: initialMcp,
  airflowConnectors: initialAirflow,
  kubernetesConnectors: initialKubernetes,
  victoriametricsConnectors: initialVictoriaMetrics,
  mattermostConnectors: initialMattermost,
  pendingInsights: initialPending,
  scenarios: initialScenarios,
  workspaceSkills: initialWorkspaceSkills,
  agents: initialAgents = [],
  localUsers: initialLocalUsers,
  currentUserId,
  workspaceId,
}: AdminPageData) {
  const { addToast } = useToast();

  const [activeSection, setActiveSection] = useState<AdminSectionId>("overview");
  const [databaseConnectors, setDatabaseConnectors] = useState(initialDb);
  const [llmConnectors, setLlmConnectors] = useState(initialLlm);
  const [documentationConnectors, setDocumentationConnectors] = useState(initialDocumentation);
  const [gitlabConnectors, setGitLabConnectors] = useState(initialGitLab);
  const [mcpConnectors, setMcpConnectors] = useState(initialMcp);
  const [airflowConnectors, setAirflowConnectors] = useState(initialAirflow);
  const [kubernetesConnectors, setKubernetesConnectors] = useState(initialKubernetes);
  const [victoriametricsConnectors, setVictoriaMetricsConnectors] = useState(
    initialVictoriaMetrics
  );
  const [mattermostConnectors, setMattermostConnectors] = useState(initialMattermost);
  const [pendingInsights, setPendingInsights] = useState(initialPending);
  const [scenarios, setScenarios] = useState(initialScenarios);
  const [workspaceSkills, setWorkspaceSkills] = useState(sortSkills(initialWorkspaceSkills));
  const [agents, setAgents] = useState(initialAgents);
  const [localUsers, setLocalUsers] = useState(sortUsers(initialLocalUsers));

  const [setupSearch, setSetupSearch] = useState("");
  const deferredSetupSearch = useDeferredValue(setupSearch);
  const [setupStatus, setSetupStatus] = useState<SetupStatusFilter>("all");
  const [connectorSheetOpen, setConnectorSheetOpen] = useState(false);
  const [connectorSheetType, setConnectorSheetType] = useState<ConnectorType | null>(null);
  const [editingConnector, setEditingConnector] = useState<AnyConnectorItem | null>(null);
  const [connectorSaving, setConnectorSaving] = useState(false);
  const [connectorStatusPendingId, setConnectorStatusPendingId] = useState<string | null>(null);
  const [documentationSyncPendingId, setDocumentationSyncPendingId] = useState<string | null>(null);
  const [connectorError, setConnectorError] = useState<string | null>(null);
  const [connectorSessionNonce, bumpConnectorSessionNonce] = useSessionNonce();

  const [skillSheetOpen, setSkillSheetOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<WorkspaceSkillItem | null>(null);
  const [skillSaving, setSkillSaving] = useState(false);
  const [skillError, setSkillError] = useState<string | null>(null);
  const [skillSessionNonce, bumpSkillSessionNonce] = useSessionNonce();

  const [agentSheetOpen, setAgentSheetOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<AgentProfile | null>(null);
  const [agentSaving, setAgentSaving] = useState(false);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [agentSessionNonce, bumpAgentSessionNonce] = useSessionNonce();

  const [userSaving, setUserSaving] = useState(false);
  const [userError, setUserError] = useState<string | null>(null);
  const [rateLimitUser, setRateLimitUser] = useState<LocalUserItem | null>(null);
  const [rateLimitSaving, setRateLimitSaving] = useState(false);
  const [rateLimitError, setRateLimitError] = useState<string | null>(null);

  const [selectedReviewId, setSelectedReviewId] = useState<string | null>(initialPending[0]?.id ?? null);
  const [reviewPendingId, setReviewPendingId] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);

  const [deleteIntent, setDeleteIntent] = useState<DeleteIntent | null>(null);
  const [deletePending, setDeletePending] = useState(false);

  const [, startSectionTransition] = useTransition();

  const readiness = useMemo(
    () =>
      buildReadinessModel({
        databaseConnectors,
        llmConnectors,
        documentationConnectors,
        gitlabConnectors,
        mcpConnectors,
        airflowConnectors,
        kubernetesConnectors,
        victoriametricsConnectors,
        mattermostConnectors,
        pendingInsights,
        scenarios,
        workspaceSkills,
        localUsers,
      }),
    [
      mattermostConnectors,
      databaseConnectors,
      documentationConnectors,
      gitlabConnectors,
      mcpConnectors,
      airflowConnectors,
      kubernetesConnectors,
      victoriametricsConnectors,
      llmConnectors,
      localUsers,
      pendingInsights,
      scenarios,
      workspaceSkills,
    ]
  );

  const officialScenarios = useMemo(
    () => sortScenariosForGovernance(scenarios),
    [scenarios]
  );

  const filteredConnectorCollections = useMemo(
    () => ({
      database: databaseConnectors.filter((item) =>
        matchesConnectorFilter(item, "database", deferredSetupSearch, setupStatus)
      ),
      llm: llmConnectors.filter((item) =>
        matchesConnectorFilter(item, "llm", deferredSetupSearch, setupStatus)
      ),
      documentation: documentationConnectors.filter((item) =>
        matchesConnectorFilter(item, "documentation", deferredSetupSearch, setupStatus)
      ),
      gitlab: gitlabConnectors.filter((item) =>
        matchesConnectorFilter(item, "gitlab", deferredSetupSearch, setupStatus)
      ),
      mcp: mcpConnectors.filter((item) =>
        matchesConnectorFilter(item, "mcp", deferredSetupSearch, setupStatus)
      ),
      airflow: airflowConnectors.filter((item) =>
        matchesConnectorFilter(item, "airflow", deferredSetupSearch, setupStatus)
      ),
      kubernetes: kubernetesConnectors.filter((item) =>
        matchesConnectorFilter(item, "kubernetes", deferredSetupSearch, setupStatus)
      ),
      victoriametrics: victoriametricsConnectors.filter((item) =>
        matchesConnectorFilter(item, "victoriametrics", deferredSetupSearch, setupStatus)
      ),
      mattermost: mattermostConnectors.filter((item) =>
        matchesConnectorFilter(item, "mattermost", deferredSetupSearch, setupStatus)
      ),
    }),
    [
      databaseConnectors,
      deferredSetupSearch,
      documentationConnectors,
      gitlabConnectors,
      mcpConnectors,
      airflowConnectors,
      kubernetesConnectors,
      victoriametricsConnectors,
      llmConnectors,
      mattermostConnectors,
      setupStatus,
    ]
  );

  const selectedReview = useMemo(
    () => pendingInsights.find((item) => item.id === selectedReviewId) ?? pendingInsights[0] ?? null,
    [pendingInsights, selectedReviewId]
  );

  useEffect(() => {
    if (!selectedReview) {
      setSelectedReviewId(null);
      return;
    }

    if (selectedReview.id !== selectedReviewId) {
      setSelectedReviewId(selectedReview.id);
    }
  }, [selectedReview, selectedReviewId]);

  const openCreateConnectorSheet = (type: ConnectorType) => {
    setConnectorError(null);
    setEditingConnector(null);
    setConnectorSheetType(type);
    setConnectorSheetOpen(true);
  };

  const openEditConnectorSheet = (type: ConnectorType, connector: AnyConnectorItem) => {
    setConnectorError(null);
    setEditingConnector(connector);
    setConnectorSheetType(type);
    setConnectorSheetOpen(true);
  };

  const openCreateSkillSheet = () => {
    setSkillError(null);
    setEditingSkill(null);
    setSkillSheetOpen(true);
  };

  const openEditSkillSheet = (skill: WorkspaceSkillItem) => {
    setSkillError(null);
    setEditingSkill(skill);
    setSkillSheetOpen(true);
  };

  const openCreateAgentSheet = () => {
    setAgentError(null);
    setEditingAgent(null);
    setAgentSheetOpen(true);
  };

  const openEditAgentSheet = (agent: AgentProfile) => {
    setAgentError(null);
    setEditingAgent(agent);
    setAgentSheetOpen(true);
  };

  async function handleConnectorSubmit(formData: FormData) {
    const connectorType = String(formData.get("connector_type") ?? "") as ConnectorType;
    const connectorId = String(formData.get("connector_id") ?? "").trim() || undefined;
    const isEdit = Boolean(connectorId);

    setConnectorSaving(true);
    setConnectorError(null);

    try {
      let savedConnector: AnyConnectorItem;

      if (connectorType === "database") {
        savedConnector = await requestJson<DatabaseConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              connection: {
                engine: normalizeDatabaseEngine(formData.get("engine")),
                host: String(formData.get("host") ?? "").trim(),
                port: Number(String(formData.get("port") ?? "8123")),
                username: String(formData.get("username") ?? "").trim(),
                password: String(formData.get("password") ?? "").trim() || null,
                usage_description: optionalText(formData.get("usage_description")),
                database: String(formData.get("database") ?? "").trim() || null,
                catalog: String(formData.get("catalog") ?? "").trim() || null,
                schema: String(formData.get("schema") ?? "").trim() || null,
                secure: formData.get("secure") !== null,
                verify: formData.get("verify") !== null,
                extra_settings: buildDatabaseExtraSettings(formData),
                extra_credentials: collectDatabaseExtraCredentials(formData),
              },
            }),
          }
        );

        startTransition(() => {
          setDatabaseConnectors((current) => upsertById(current, savedConnector as DatabaseConnectorItem));
        });
      } else if (connectorType === "llm") {
        const existingExtraSettings =
          editingConnector != null && editingConnector.id === connectorId
            ? ((editingConnector as LlmConnectorItem).connection.extra_settings ?? {})
            : {};
        savedConnector = await requestJson<LlmConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              is_default: formData.get("is_default") !== null,
              connection: {
                base_url: String(formData.get("base_url") ?? "").trim(),
                model: String(formData.get("model") ?? "").trim(),
                api_key: null,
                api_keys: collectLlmApiKeys(formData),
                retained_api_key_indexes: collectRetainedLlmApiKeyIndexes(formData),
                endpoint_path: String(formData.get("endpoint_path") ?? "").trim() || "/chat/completions",
                verify_tls: formData.get("verify_tls") !== null,
                temperature: optionalNumber(formData.get("temperature")),
                top_p: optionalNumber(formData.get("top_p")),
                reasoning_effort: String(formData.get("reasoning_effort") ?? "") || null,
                max_tokens: optionalNumber(formData.get("max_tokens")),
                max_tokens_parameter:
                  String(formData.get("max_tokens_parameter") ?? "max_tokens") ===
                  "max_completion_tokens"
                    ? "max_completion_tokens"
                    : "max_tokens",
                headers: parseLlmHeaders(formData.get("llm_headers_json")),
                extra_settings: buildLlmExtraSettings(formData, existingExtraSettings),
              },
            }),
          }
        );

        startTransition(() => {
          setLlmConnectors((current) =>
            upsertLlmConnector(current, savedConnector as LlmConnectorItem)
          );
        });
      } else if (connectorType === "documentation") {
        const authTypeValue = String(formData.get("auth_type") ?? "none");
        const authType =
          authTypeValue === "bearer" || authTypeValue === "basic" ? authTypeValue : "none";
        savedConnector = await requestJson<DocumentationConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              visible_to_default_assistant: formData.get("visible_to_default_assistant") !== null,
              connection: {
                provider: "docsy_hugo",
                base_url: String(formData.get("base_url") ?? "").trim(),
                index_url: optionalText(formData.get("index_url")),
                content_format: "markdown",
                username: optionalText(formData.get("username")),
                api_token: optionalText(formData.get("api_token")),
                usage_description: optionalText(formData.get("usage_description")),
                auth_type: authType,
                verify_tls: formData.get("verify_tls") !== null,
                include_paths: splitCommaList(formData.get("include_paths")),
                exclude_paths: splitCommaList(formData.get("exclude_paths")),
                sync_enabled: formData.get("sync_enabled") !== null,
                sync_interval_minutes: Number(String(formData.get("sync_interval_minutes") ?? "60")),
                qdrant_base_url: String(formData.get("qdrant_base_url") ?? "").trim() || "http://qdrant:6333",
                qdrant_api_key: optionalText(formData.get("qdrant_api_key")),
                qdrant_collection_name:
                  String(formData.get("qdrant_collection_name") ?? "").trim() || "documentation_chunks",
                qdrant_prefer_grpc: formData.get("qdrant_prefer_grpc") !== null,
                vector_size: Number(String(formData.get("vector_size") ?? "1024")),
                embedding_base_url: optionalText(formData.get("embedding_base_url")),
                embedding_api_key: optionalText(formData.get("embedding_api_key")),
                embedding_model:
                  String(formData.get("embedding_model") ?? "").trim() || "BAAI/bge-small-en-v1.5",
                embedding_endpoint_path:
                  String(formData.get("embedding_endpoint_path") ?? "").trim() || "/v1/embeddings",
                embedding_timeout_seconds: Number(
                  String(formData.get("embedding_timeout_seconds") ?? "120")
                ),
                extra_settings: {},
              },
            }),
          }
        );

        startTransition(() => {
          setDocumentationConnectors((current) =>
            upsertById(current, savedConnector as DocumentationConnectorItem)
          );
        });
      } else if (connectorType === "gitlab") {
        savedConnector = await requestJson<GitLabConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              connection: {
                provider: "gitlab",
                base_url: String(formData.get("base_url") ?? "").trim(),
                api_token: String(formData.get("api_token") ?? "").trim() || null,
                use_when: String(formData.get("use_when") ?? "").trim(),
                verify_tls: formData.get("verify_tls") !== null,
                default_project_paths: splitCommaList(formData.get("default_project_paths")),
                default_group_paths: splitCommaList(formData.get("default_group_paths")),
                search_limit: Number(String(formData.get("search_limit") ?? "5")),
                read_max_tokens: Number(String(formData.get("read_max_tokens") ?? "1600")),
                extra_settings: {},
              },
            }),
          }
        );

        startTransition(() => {
          setGitLabConnectors((current) =>
            upsertById(current, savedConnector as GitLabConnectorItem)
          );
        });
      } else if (connectorType === "mcp") {
        const providerValue = String(formData.get("provider") ?? "generic");
        savedConnector = await requestJson<McpConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              visible_to_external_mcp: formData.get("visible_to_external_mcp") !== null,
              connection: {
                provider:
                  providerValue === "jira" ||
                  providerValue === "confluence" ||
                  providerValue === "gitlab"
                    ? providerValue
                    : "generic",
                transport: "streamable-http",
                url: String(formData.get("url") ?? "").trim(),
                verify_tls: formData.get("verify_tls") !== null,
                headers: parseJsonObjectField(formData.get("headers_json")),
                secret_headers: parseMcpSecretHeaders(formData.get("secret_headers_json")),
                enabled_groups: optionalText(formData.get("enabled_groups")),
                allowed_tool_names: splitCommaList(formData.get("allowed_tool_names")),
                timeout_seconds: Number(String(formData.get("timeout_seconds") ?? "30")),
                tool_name_prefix: optionalText(formData.get("tool_name_prefix")),
                llm_connector_name: optionalText(formData.get("llm_connector_name")),
                usage_description: optionalText(formData.get("usage_description")),
                extra_settings: {},
              },
            }),
          }
        );

        startTransition(() => {
          setMcpConnectors((current) => upsertById(current, savedConnector as McpConnectorItem));
        });
      } else if (connectorType === "airflow") {
        savedConnector = await requestJson<AirflowConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              connection: {
                provider: "airflow",
                base_url: String(formData.get("base_url") ?? "").trim(),
                username: String(formData.get("username") ?? "").trim(),
                password: String(formData.get("password") ?? "").trim() || null,
                use_when: String(formData.get("use_when") ?? "").trim(),
                auth_type: "basic",
                verify_tls: formData.get("verify_tls") !== null,
                api_path: String(formData.get("api_path") ?? "").trim() || "/api/v1",
                dag_limit: Number(String(formData.get("dag_limit") ?? "25")),
                dag_run_limit: Number(String(formData.get("dag_run_limit") ?? "20")),
                task_limit: Number(String(formData.get("task_limit") ?? "50")),
                log_max_chars: Number(String(formData.get("log_max_chars") ?? "8000")),
                timeout_seconds: Number(String(formData.get("timeout_seconds") ?? "20")),
                extra_settings: {},
              },
            }),
          }
        );

        startTransition(() => {
          setAirflowConnectors((current) =>
            upsertById(current, savedConnector as AirflowConnectorItem)
          );
        });
      } else if (connectorType === "kubernetes") {
        savedConnector = await requestJson<KubernetesConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              visible_to_default_assistant:
                formData.get("visible_to_default_assistant") !== null,
              connection: {
                provider: "kubernetes",
                base_url: String(formData.get("base_url") ?? "").trim(),
                bearer_token: String(formData.get("bearer_token") ?? "").trim() || null,
                use_when: String(formData.get("use_when") ?? "").trim(),
                auth_type: "bearer",
                verify_tls: formData.get("verify_tls") !== null,
                namespace_allowlist: splitCommaList(formData.get("namespace_allowlist")),
                default_limit: Number(String(formData.get("default_limit") ?? "25")),
                max_limit: Number(String(formData.get("max_limit") ?? "100")),
                log_max_chars: Number(String(formData.get("log_max_chars") ?? "8000")),
                timeout_seconds: Number(String(formData.get("timeout_seconds") ?? "20")),
                extra_settings: {},
              },
            }),
          }
        );

        startTransition(() => {
          setKubernetesConnectors((current) =>
            upsertById(current, savedConnector as KubernetesConnectorItem)
          );
        });
      } else if (connectorType === "victoriametrics") {
        const authType = formData.get("auth_type") === "bearer" ? "bearer" : "none";
        savedConnector = await requestJson<VictoriaMetricsConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              visible_to_default_assistant:
                formData.get("visible_to_default_assistant") !== null,
              connection: {
                provider: "victoriametrics",
                base_url: String(formData.get("base_url") ?? "").trim(),
                bearer_token: String(formData.get("bearer_token") ?? "").trim() || null,
                use_when: String(formData.get("use_when") ?? "").trim(),
                auth_type: authType,
                verify_tls: formData.get("verify_tls") !== null,
                query_path:
                  String(formData.get("query_path") ?? "").trim() ||
                  "/prometheus/api/v1/query",
                query_range_path:
                  String(formData.get("query_range_path") ?? "").trim() ||
                  "/prometheus/api/v1/query_range",
                templates: parseJsonArrayField(formData.get("templates")),
                timeout_seconds: Number(String(formData.get("timeout_seconds") ?? "20")),
                max_series: Number(String(formData.get("max_series") ?? "20")),
                max_datapoints: Number(String(formData.get("max_datapoints") ?? "300")),
                extra_settings: {},
              },
            }),
          }
        );

        startTransition(() => {
          setVictoriaMetricsConnectors((current) =>
            upsertById(current, savedConnector as VictoriaMetricsConnectorItem)
          );
        });
      } else {
        savedConnector = await requestJson<MattermostConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              agent_id: String(formData.get("agent_id") ?? "").trim() || null,
              connection: {
                provider: "mattermost",
                base_url: String(formData.get("base_url") ?? "").trim(),
                bot_token: String(formData.get("bot_token") ?? "").trim() || null,
                bot_username: String(formData.get("bot_username") ?? "").trim() || null,
                verify_tls: formData.get("verify_tls") !== null,
                listen_enabled: formData.get("listen_enabled") !== null,
                send_sql_enabled: formData.get("send_sql_enabled") !== null,
                ambient_listen_enabled: formData.get("ambient_listen_enabled") !== null,
                ambient_channel_ids: splitCommaList(formData.get("ambient_channel_ids")),
                ambient_decision_prompt: optionalText(formData.get("ambient_decision_prompt")),
                extra_settings: {},
              },
            }),
          }
        );

        startTransition(() => {
          setMattermostConnectors((current) =>
            upsertById(current, savedConnector as MattermostConnectorItem)
          );
        });
      }

      setConnectorSheetOpen(false);
      setEditingConnector(null);
      bumpConnectorSessionNonce();
      addToast({
        type: "success",
        title: isEdit ? "Подключение обновлено" : "Подключение создано",
        description: `${savedConnector.name} теперь в конфигурации рабочего пространства`,
      });
    } catch (error) {
      setConnectorError(error instanceof Error ? error.message : "Не удалось сохранить подключение");
    } finally {
      setConnectorSaving(false);
    }
  }

  async function handleSkillSubmit(formData: FormData) {
    setSkillSaving(true);
    setSkillError(null);

    const skillId = String(formData.get("skill_id") ?? "").trim();
    const sourceKind = String(formData.get("source_kind") ?? "github_markdown_url");
    const capabilityFamily = String(
      formData.get("capability_family") ?? "sql_optimization_advisor"
    );
    const manualMarkdown = String(formData.get("content_markdown") ?? "");
    const manualUseWhen = String(formData.get("use_when") ?? "").trim();
    const manualContentMarkdown =
      sourceKind === "manual_markdown"
        ? withManualSkillUseWhen(manualMarkdown, manualUseWhen)
        : "";
    if (
      sourceKind === "manual_markdown" &&
      utf8ByteLength(manualContentMarkdown) > MAX_MANUAL_SKILL_CONTENT_BYTES
    ) {
      setSkillError("Текст навыка слишком велик после добавления подсказки о применении");
      setSkillSaving(false);
      return;
    }
    const payload = {
      workspace_id: workspaceId,
      name: String(formData.get("name") ?? "").trim(),
      capability_family: capabilityFamily,
      engine_scope:
        capabilityFamily === "chat_instruction"
          ? "any"
          : String(formData.get("engine_scope") ?? "clickhouse"),
      priority: Number(String(formData.get("priority") ?? "100")),
      source_kind: sourceKind,
      status: String(formData.get("status") ?? "active"),
      visible_to_default_assistant:
        capabilityFamily === "chat_instruction"
          ? formData.get("visible_to_default_assistant") !== null
          : true,
      ...(sourceKind === "manual_markdown"
        ? {
            content_markdown: manualContentMarkdown,
          }
        : {
            source_url: String(formData.get("source_url") ?? "").trim(),
          }),
    };

    try {
      const saved = await requestJson<WorkspaceSkillItem>(
        skillId ? `/api/admin/skills/${skillId}?workspace_id=${workspaceId}` : "/api/admin/skills",
        {
          method: skillId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );

      startTransition(() => {
        setWorkspaceSkills((current) => sortSkills(upsertById(current, saved)));
      });
      setSkillSheetOpen(false);
      setEditingSkill(null);
      bumpSkillSessionNonce();
      addToast({
        type: "success",
        title: skillId ? "Навык обновлён" : "Навык создан",
        description: `${saved.name} готов к проверке`,
      });
    } catch (error) {
      setSkillError(error instanceof Error ? error.message : "Не удалось сохранить навык");
    } finally {
      setSkillSaving(false);
    }
  }

  async function handleAgentSubmit(formData: FormData) {
    setAgentSaving(true);
    setAgentError(null);
    const agentId = String(formData.get("agent_id") ?? "").trim();

    try {
      const saved = await requestJson<AgentProfile>(
        agentId ? `/api/admin/agents/${agentId}?workspace_id=${workspaceId}` : "/api/admin/agents",
        {
          method: agentId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workspace_id: workspaceId,
            slug: String(formData.get("slug") ?? "").trim(),
            title: String(formData.get("title") ?? "").trim(),
            description: String(formData.get("description") ?? "").trim() || null,
            admin_prompt: String(formData.get("admin_prompt") ?? "").trim() || null,
            is_active: formData.get("is_active") !== null,
            llm_connector_name: String(formData.get("llm_connector_name") ?? "").trim() || null,
            allowed_database_connector_names: selectedValues(formData, "database_connectors"),
            allowed_documentation_connector_names: selectedValues(formData, "documentation_connectors"),
            allowed_gitlab_connector_names: selectedValues(formData, "gitlab_connectors"),
            allowed_mcp_connector_names: selectedValues(formData, "mcp_connectors"),
            allowed_airflow_connector_names: selectedValues(formData, "airflow_connectors"),
            allowed_kubernetes_connector_names: selectedValues(formData, "kubernetes_connectors"),
            allowed_victoriametrics_connector_names: selectedValues(
              formData,
              "victoriametrics_connectors"
            ),
            allowed_workspace_skill_ids: selectedValues(formData, "workspace_skill_ids"),
            allowed_rag_connector_names: [],
            settings: {
              run_sql_allowed: formData.get("run_sql_allowed") !== null,
              temperature: optionalNumber(formData.get("temperature")),
              max_tokens: optionalNumber(formData.get("max_tokens")),
            },
          }),
        }
      );

      startTransition(() => {
        setAgents((current) => upsertById(current, saved));
      });
      setAgentSheetOpen(false);
      setEditingAgent(null);
      bumpAgentSessionNonce();
      addToast({
        type: "success",
        title: agentId ? "Агент обновлён" : "Агент создан",
        description: saved.title,
      });
    } catch (error) {
      setAgentError(error instanceof Error ? error.message : "Не удалось сохранить агента");
    } finally {
      setAgentSaving(false);
    }
  }

  async function handleConnectorActiveToggle(
    type: ConnectorType,
    connector: AnyConnectorItem,
    isActive: boolean
  ) {
    setConnectorStatusPendingId(connector.id);

    try {
      const savedConnector = await requestJson<AnyConnectorItem>(
        connectorEndpoint(type, connector.id, workspaceId),
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildConnectorSaveBody(connector, type, workspaceId, isActive)),
        }
      );

      startTransition(() => {
        if (type === "database") {
          setDatabaseConnectors((current) => upsertById(current, savedConnector as DatabaseConnectorItem));
        } else if (type === "llm") {
          setLlmConnectors((current) =>
            upsertLlmConnector(current, savedConnector as LlmConnectorItem)
          );
        } else if (type === "documentation") {
          setDocumentationConnectors((current) =>
            upsertById(current, savedConnector as DocumentationConnectorItem)
          );
        } else if (type === "gitlab") {
          setGitLabConnectors((current) => upsertById(current, savedConnector as GitLabConnectorItem));
        } else if (type === "mcp") {
          setMcpConnectors((current) => upsertById(current, savedConnector as McpConnectorItem));
        } else if (type === "airflow") {
          setAirflowConnectors((current) => upsertById(current, savedConnector as AirflowConnectorItem));
        } else if (type === "kubernetes") {
          setKubernetesConnectors((current) =>
            upsertById(current, savedConnector as KubernetesConnectorItem)
          );
        } else if (type === "victoriametrics") {
          setVictoriaMetricsConnectors((current) =>
            upsertById(current, savedConnector as VictoriaMetricsConnectorItem)
          );
        } else {
          setMattermostConnectors((current) =>
            upsertById(current, savedConnector as MattermostConnectorItem)
          );
        }
      });
      addToast({
        type: "success",
        title: isActive ? "Подключение включено" : "Подключение отключено",
        description: `${savedConnector.name} ${isActive ? "доступно при выполнении" : "скрыто от выполнения"}`,
      });
    } catch (error) {
      addToast({
        type: "error",
        title: "Не удалось изменить статус подключения",
        description: error instanceof Error ? error.message : "Не удалось обновить статус",
      });
    } finally {
      setConnectorStatusPendingId(null);
    }
  }

  async function handleDocumentationSync(connector: DocumentationConnectorItem) {
    setDocumentationSyncPendingId(connector.id);

    try {
      await requestJson<unknown>(
        `/api/admin/documentation-connectors/${connector.id}/sync?workspace_id=${workspaceId}`,
        { method: "POST" }
      );
      addToast({
        type: "success",
        title: "Синхронизация документации поставлена в очередь",
        description: connector.name,
      });
    } catch (error) {
      addToast({
        type: "error",
        title: "Не удалось синхронизировать документацию",
        description: error instanceof Error ? error.message : "Не удалось запустить синхронизацию",
      });
    } finally {
      setDocumentationSyncPendingId(null);
    }
  }

  async function handleMcpSync(connector: McpConnectorItem) {
    setDocumentationSyncPendingId(connector.id);

    try {
      await requestJson<unknown>(
        `/api/admin/mcp-connectors/${connector.id}/sync?workspace_id=${workspaceId}`,
        { method: "POST" }
      );
      const refreshed = await requestJson<McpConnectorItem[]>(
        `/api/admin/mcp-connectors?workspace_id=${workspaceId}&limit=200`
      );
      startTransition(() => setMcpConnectors(refreshed));
      addToast({
        type: "success",
        title: "Инструменты MCP синхронизированы",
        description: connector.name,
      });
    } catch (error) {
      addToast({
        type: "error",
        title: "Не удалось синхронизировать MCP",
        description: error instanceof Error ? error.message : "Не удалось синхронизировать",
      });
    } finally {
      setDocumentationSyncPendingId(null);
    }
  }

  async function handleCreateLocalUser(formData: FormData, form: HTMLFormElement) {
    const login = String(formData.get("login") ?? "").trim();
    const tempId = createTempId("user");
    const optimisticUser: LocalUserItem = {
      id: tempId,
      workspace_id: workspaceId,
      login,
      role: String(formData.get("role") ?? "admin"),
      is_active: true,
      auth_provider: "local",
      auth_subject: `local:${login}`,
      display_name: String(formData.get("display_name") ?? "").trim() || null,
      api_rate_limit_per_hour: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    setUserSaving(true);
    setUserError(null);
    startTransition(() => {
      setLocalUsers((current) => sortUsers([...current, optimisticUser]));
    });

    try {
      const createdUser = await requestJson<LocalUserItem>("/api/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          login,
          password: String(formData.get("password") ?? ""),
          role: String(formData.get("role") ?? "admin"),
          display_name: String(formData.get("display_name") ?? "").trim() || null,
        }),
      });

      startTransition(() => {
        setLocalUsers((current) =>
          sortUsers(current.map((item) => (item.id === tempId ? createdUser : item)))
        );
      });
      form.reset();
      addToast({
        type: "success",
        title: "Пользователь создан",
        description: `${createdUser.login} теперь может войти`,
      });
    } catch (error) {
      startTransition(() => {
        setLocalUsers((current) => current.filter((item) => item.id !== tempId));
      });
      setUserError(error instanceof Error ? error.message : "Не удалось создать пользователя");
    } finally {
      setUserSaving(false);
    }
  }

  async function handleSaveUserRateLimit(requestsPerHour: number | null) {
    if (!rateLimitUser) {
      return;
    }
    setRateLimitSaving(true);
    setRateLimitError(null);
    try {
      const updated = await requestJson<LocalUserItem>(
        `/api/admin/users/${rateLimitUser.id}/rate-limit`,
        requestsPerHour == null
          ? { method: "DELETE" }
          : {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ requests_per_hour: requestsPerHour }),
            }
      );
      startTransition(() => {
        setLocalUsers((current) =>
          sortUsers(current.map((item) => (item.id === updated.id ? updated : item)))
        );
      });
      setRateLimitUser(null);
      addToast({
        type: "success",
        title: requestsPerHour == null ? "Лимит API отключён" : "Лимит API сохранён",
        description:
          requestsPerHour == null
            ? `${updated.login} без ограничений.`
            : `${updated.login}: ${requestsPerHour} запросов за последние 60 минут.`,
      });
    } catch (error) {
      setRateLimitError(error instanceof Error ? error.message : "Не удалось обновить лимит API");
    } finally {
      setRateLimitSaving(false);
    }
  }

  async function handleResetUserRateLimit() {
    if (!rateLimitUser) {
      return;
    }
    setRateLimitSaving(true);
    setRateLimitError(null);
    try {
      const updated = await requestJson<LocalUserItem>(
        `/api/admin/users/${rateLimitUser.id}/rate-limit/reset`,
        { method: "POST" }
      );
      setRateLimitUser(updated);
      addToast({
        type: "success",
        title: "Лимит API сброшен",
        description: `${updated.login} снова доступен полный лимит запросов за последние 60 минут.`,
      });
    } catch (error) {
      setRateLimitError(error instanceof Error ? error.message : "Не удалось сбросить использование API");
    } finally {
      setRateLimitSaving(false);
    }
  }

  async function handleReview(recordId: string, action: "approve" | "reject") {
    const existing = pendingInsights;
    const removed = pendingInsights.find((item) => item.id === recordId);
    if (!removed) {
      return;
    }

    setReviewPendingId(recordId);
    setReviewError(null);
    startTransition(() => {
      setPendingInsights((current) => current.filter((item) => item.id !== recordId));
    });

    try {
      await requestJson<unknown>(`/api/admin/rag-insights/${recordId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspaceId, verified_by: "admin" }),
      });
      addToast({
        type: action === "approve" ? "success" : "warning",
        title: action === "approve" ? "Инсайт подтверждён" : "Инсайт отклонён",
        description: removed.summary,
      });
    } catch (error) {
      startTransition(() => {
        setPendingInsights(existing);
      });
      setReviewError(error instanceof Error ? error.message : "Не удалось обработать инсайт");
    } finally {
      setReviewPendingId(null);
    }
  }

  async function confirmDeleteIntent() {
    if (!deleteIntent) {
      return;
    }

    setDeletePending(true);

    try {
      if (deleteIntent.kind === "connector") {
        if (deleteIntent.connectorType === "database") {
          const previous = databaseConnectors;
          startTransition(() => {
            setDatabaseConnectors((current) =>
              current.filter((item) => item.id !== deleteIntent.id)
            );
          });

          try {
            await requestJson<unknown>(
              connectorEndpoint(deleteIntent.connectorType, deleteIntent.id, workspaceId),
              { method: "DELETE" }
            );
          } catch (error) {
            startTransition(() => {
              setDatabaseConnectors(previous);
            });
            throw error;
          }
        }

        if (deleteIntent.connectorType === "llm") {
          const previous = llmConnectors;
          startTransition(() => {
            setLlmConnectors((current) =>
              current.filter((item) => item.id !== deleteIntent.id)
            );
          });

          try {
            await requestJson<unknown>(
              connectorEndpoint(deleteIntent.connectorType, deleteIntent.id, workspaceId),
              { method: "DELETE" }
            );
          } catch (error) {
            startTransition(() => {
              setLlmConnectors(previous);
            });
            throw error;
          }
        }

        if (deleteIntent.connectorType === "documentation") {
          const previous = documentationConnectors;
          startTransition(() => {
            setDocumentationConnectors((current) =>
              current.filter((item) => item.id !== deleteIntent.id)
            );
          });

          try {
            await requestJson<unknown>(
              connectorEndpoint(deleteIntent.connectorType, deleteIntent.id, workspaceId),
              { method: "DELETE" }
            );
          } catch (error) {
            startTransition(() => {
              setDocumentationConnectors(previous);
            });
            throw error;
          }
        }

        if (deleteIntent.connectorType === "gitlab") {
          const previous = gitlabConnectors;
          startTransition(() => {
            setGitLabConnectors((current) =>
              current.filter((item) => item.id !== deleteIntent.id)
            );
          });

          try {
            await requestJson<unknown>(
              connectorEndpoint(deleteIntent.connectorType, deleteIntent.id, workspaceId),
              { method: "DELETE" }
            );
          } catch (error) {
            startTransition(() => {
              setGitLabConnectors(previous);
            });
            throw error;
          }
        }

        if (deleteIntent.connectorType === "mcp") {
          const previous = mcpConnectors;
          startTransition(() => {
            setMcpConnectors((current) =>
              current.filter((item) => item.id !== deleteIntent.id)
            );
          });

          try {
            await requestJson<unknown>(
              connectorEndpoint(deleteIntent.connectorType, deleteIntent.id, workspaceId),
              { method: "DELETE" }
            );
          } catch (error) {
            startTransition(() => {
              setMcpConnectors(previous);
            });
            throw error;
          }
        }

        if (deleteIntent.connectorType === "airflow") {
          const previous = airflowConnectors;
          startTransition(() => {
            setAirflowConnectors((current) =>
              current.filter((item) => item.id !== deleteIntent.id)
            );
          });

          try {
            await requestJson<unknown>(
              connectorEndpoint(deleteIntent.connectorType, deleteIntent.id, workspaceId),
              { method: "DELETE" }
            );
          } catch (error) {
            startTransition(() => {
              setAirflowConnectors(previous);
            });
            throw error;
          }
        }

        if (deleteIntent.connectorType === "kubernetes") {
          const previous = kubernetesConnectors;
          startTransition(() => {
            setKubernetesConnectors((current) =>
              current.filter((item) => item.id !== deleteIntent.id)
            );
          });

          try {
            await requestJson<unknown>(
              connectorEndpoint(deleteIntent.connectorType, deleteIntent.id, workspaceId),
              { method: "DELETE" }
            );
          } catch (error) {
            startTransition(() => {
              setKubernetesConnectors(previous);
            });
            throw error;
          }
        }

        if (deleteIntent.connectorType === "victoriametrics") {
          const previous = victoriametricsConnectors;
          startTransition(() => {
            setVictoriaMetricsConnectors((current) =>
              current.filter((item) => item.id !== deleteIntent.id)
            );
          });

          try {
            await requestJson<unknown>(
              connectorEndpoint(deleteIntent.connectorType, deleteIntent.id, workspaceId),
              { method: "DELETE" }
            );
          } catch (error) {
            startTransition(() => {
              setVictoriaMetricsConnectors(previous);
            });
            throw error;
          }
        }

        if (deleteIntent.connectorType === "mattermost") {
          const previous = mattermostConnectors;
          startTransition(() => {
            setMattermostConnectors((current) =>
              current.filter((item) => item.id !== deleteIntent.id)
            );
          });

          try {
            await requestJson<unknown>(
              connectorEndpoint(deleteIntent.connectorType, deleteIntent.id, workspaceId),
              { method: "DELETE" }
            );
          } catch (error) {
            startTransition(() => {
              setMattermostConnectors(previous);
            });
            throw error;
          }
        }

        addToast({
          type: "success",
          title: "Подключение удалено",
          description: deleteIntent.title,
        });
      }

      if (deleteIntent.kind === "skill") {
        const previous = workspaceSkills;
        startTransition(() => {
          setWorkspaceSkills((current) => current.filter((item) => item.id !== deleteIntent.id));
        });

        try {
          await requestJson<unknown>(`/api/admin/skills/${deleteIntent.id}?workspace_id=${workspaceId}`, {
            method: "DELETE",
          });
          addToast({
            type: "success",
            title: "Навык удалён",
            description: deleteIntent.title,
          });
        } catch (error) {
          startTransition(() => {
            setWorkspaceSkills(previous);
          });
          throw error;
        }
      }

      if (deleteIntent.kind === "user") {
        const previous = localUsers;
        startTransition(() => {
          setLocalUsers((current) => current.filter((item) => item.id !== deleteIntent.user.id));
        });

        try {
          await requestJson<unknown>(`/api/admin/users/${deleteIntent.user.id}`, {
            method: "DELETE",
          });
          addToast({
            type: "success",
            title: "Пользователь удалён",
            description: deleteIntent.user.login,
          });
        } catch (error) {
          startTransition(() => {
            setLocalUsers(previous);
          });
          throw error;
        }
      }

      if (deleteIntent.kind === "scenario") {
        const previous = scenarios;
        startTransition(() => {
          setScenarios((current) =>
            current.map((scenario) =>
              scenario.id === deleteIntent.scenario.id
                ? { ...scenario, status: "archived" as const }
                : scenario
            )
          );
        });

        try {
          const archived = await archiveScenario(deleteIntent.scenario.id, workspaceId);
          startTransition(() => {
            setScenarios((current) => current.map((scenario) => (scenario.id === archived.id ? archived : scenario)));
          });
          addToast({
            type: "success",
            title: "Сценарий отправлен в архив",
            description: deleteIntent.scenario.title,
          });
        } catch (error) {
          startTransition(() => {
            setScenarios(previous);
          });
          throw error;
        }
      }

      if (deleteIntent.kind === "agent") {
        const previous = agents;
        startTransition(() => {
          setAgents((current) => current.filter((agent) => agent.id !== deleteIntent.agent.id));
        });
        try {
          await requestJson(`/api/admin/agents/${deleteIntent.agent.id}?workspace_id=${workspaceId}`, {
            method: "DELETE",
          });
          addToast({
            type: "success",
            title: "Агент удалён",
            description: deleteIntent.agent.title,
          });
        } catch (error) {
          startTransition(() => {
            setAgents(previous);
          });
          throw error;
        }
      }

      setDeleteIntent(null);
    } catch (error) {
      addToast({
        type: "error",
        title: "Не удалось удалить",
        description: error instanceof Error ? error.message : "Неизвестная ошибка",
      });
    } finally {
      setDeletePending(false);
    }
  }

  return (
    <>
      <main className="min-h-screen bg-[radial-gradient(circle_at_top_right,rgb(var(--color-accent)/0.18),transparent_28%),radial-gradient(circle_at_20%_0%,rgb(var(--color-hover)/0.32),transparent_30%),linear-gradient(180deg,rgb(var(--color-page))_0%,rgb(var(--color-page))_100%)] px-4 py-5 text-primary md:px-6 lg:px-8">
        <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-6">
          <header className="relative rounded-2xl border border-border bg-surface/[0.92] px-4 py-4 shadow-[0_18px_52px_rgb(var(--color-shadow)/calc(0.24*var(--shadow-opacity-scale)))] md:px-5">
            <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <SectionBadge tone={readiness.blockers.length === 0 ? "success" : "warning"}>
                    {readiness.blockers.length === 0 ? "готов" : `блокеров: ${readiness.blockers.length}`}
                  </SectionBadge>
                  <span className="min-w-0 truncate text-sm text-secondary">Рабочее пространство `{workspaceId}`</span>
                </div>
                <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                  <div className="min-w-0">
                    <h1 className="mt-6 text-2xl font-semibold text-primary md:text-3xl">Администрирование</h1>
                    <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">
                      Настройка выполнения, управление, доступ и проверка — в одном месте.
                    </p>
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap justify-end gap-2">
                <ThemeSelector />
                <Button onClick={() => startSectionTransition(() => setActiveSection("setup"))}>
                  Исправить настройки
                </Button>
                <Button
                  variant="outline"
                  onClick={() => startSectionTransition(() => setActiveSection("review"))}
                >
                  Очистить очередь проверки
                </Button>
              </div>
              <Link
                href="/"
                aria-label="Перейти в рабочее пространство"
                title="Перейти в рабочее пространство"
                className="absolute right-4 top-4 z-10 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-accent/50 bg-elevated text-primary shadow-[0_10px_28px_rgb(var(--color-accent)/0.14)] transition hover:bg-border-strong md:right-5"
              >
                <X className="h-4 w-4" />
              </Link>
            </div>
          </header>

          <div className="flex flex-wrap gap-2 rounded-xl border border-border bg-surface/70 p-2">
            <CompactSummaryStat label="Оценка готовности" value={`${readiness.readinessScore}%`} />
            <CompactSummaryStat
              label="Защищено"
              value={`${readiness.totalSecureConnections}/${readiness.totalConnections}`}
            />
            <CompactSummaryStat label="Проверка" value={pendingInsights.length} />
          </div>

          <div className="sticky top-4 z-20 rounded-2xl border border-border bg-surface/90 p-1.5 backdrop-blur-xl">
            <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-5">
              {SECTION_META.map((section) => {
                const isActive = section.id === activeSection;
                return (
                  <button
                    key={section.id}
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => startSectionTransition(() => setActiveSection(section.id))}
                    className={`rounded-xl px-3 py-2.5 text-left transition ${
                      isActive
                        ? "bg-elevated text-primary shadow-[0_10px_28px_rgb(var(--color-accent)/0.16)]"
                        : "bg-transparent text-secondary hover:bg-surface hover:text-primary"
                    }`}
                  >
                    <div className="text-sm font-medium">{section.label}</div>
                    <div className="mt-1 hidden text-xs leading-5 opacity-80 xl:block">{section.description}</div>
                  </button>
                );
              })}
            </div>
          </div>

          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={activeSection}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="grid gap-6 pb-8"
            >
              {activeSection === "overview" ? (
                <SectionShell
                  eyebrow="Обзор"
                  title="Управляйте рабочим пространством, а не страницей"
                  description="Сначала готовность и разбор, ниже дополнительные счётчики"
                >
                  <div className="grid gap-4 xl:grid-cols-4">
                    <MetricCard
                      icon={<Sparkles className="h-5 w-5" />}
                      title="Готовность"
                      value={`${readiness.readinessScore}%`}
                      caption="Взвешено по базовым проверкам выполнения и управления"
                      tone="primary"
                    />
                    <MetricCard
                      icon={<Shield className="h-5 w-5" />}
                      title="Защищённые подключения"
                      value={`${readiness.totalSecureConnections}/${readiness.totalConnections}`}
                      caption="Подключения с включённым TLS"
                      tone={readiness.totalSecureConnections === readiness.totalConnections ? "success" : "warning"}
                    />
                    <MetricCard
                      icon={<Workflow className="h-5 w-5" />}
                      title="Опубликованные сценарии"
                      value={readiness.publishedScenarioCount}
                      caption="Официальные процессы, доступные аналитикам"
                      tone={readiness.publishedScenarioCount > 0 ? "success" : "warning"}
                    />
                    <MetricCard
                      icon={<Clock3 className="h-5 w-5" />}
                      title="Очередь проверки"
                      value={pendingInsights.length}
                      caption="Инсайты, ожидающие проверки"
                      tone={pendingInsights.length === 0 ? "success" : "warning"}
                    />
                  </div>

                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
                    <InfoPanel
                      title="Критические блокеры"
                      description="Эти пункты снижают готовность и обычно мешают аналитикам"
                    >
                      {readiness.blockers.length > 0 ? (
                        <div className="grid gap-3">
                          {readiness.blockers.map((blocker) => (
                            <div
                              key={blocker}
                              className="rounded-[22px] border border-danger-border bg-purple-subtle px-4 py-4"
                            >
                              <div className="flex items-start gap-3">
                                <XCircle className="mt-0.5 h-5 w-5 flex-shrink-0 text-danger" />
                                <p className="text-sm leading-6 text-danger">{blocker}</p>
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <EmptyState
                          icon={<CheckCircle2 className="h-5 w-5" />}
                          title="Блокеров нет"
                          description="Настройки в порядке. Держите очередь проверки и слой управления под контролем"
                          className="border-success-border bg-[linear-gradient(180deg,rgb(var(--color-cyan-subtle)/0.92),rgb(var(--color-cyan-subtle)/0.98))]"
                        />
                      )}
                    </InfoPanel>

                    <InfoPanel
                      title="Состояние системы"
                      description="Быстрый взгляд на важные части"
                    >
                      <div className="grid gap-3">
                        <SummaryRow label="Подключения к базам данных" value={databaseConnectors.length} />
                        <SummaryRow label="Подключения LLM" value={llmConnectors.length} />
                        <SummaryRow label="Подключения документации" value={documentationConnectors.length} />
                        <SummaryRow label="Подключения GitLab" value={gitlabConnectors.length} />
                        <SummaryRow label="Подключения MCP" value={mcpConnectors.length} />
                        <SummaryRow label="Подключения Airflow" value={airflowConnectors.length} />
                        <SummaryRow label="Подключения Kubernetes" value={kubernetesConnectors.length} />
                        <SummaryRow label="Подключения VictoriaMetrics" value={victoriametricsConnectors.length} />
                        <SummaryRow label="Подключения Tag" value={mattermostConnectors.length} />
                        <SummaryRow label="Активные навыки" value={readiness.activeSkillCount} />
                        <SummaryRow label="Локальные пользователи" value={localUsers.length} />
                      </div>
                    </InfoPanel>
                  </div>
                </SectionShell>
              ) : null}

              {activeSection === "setup" ? (
                <SectionShell
                  eyebrow="Настройки"
                  title="Конфигурация выполнения"
                  description="Поиск, фильтрация, редактирование и удаление подключений без перехода со страницы"
                  actions={
                    <>
                      <Button onClick={() => openCreateConnectorSheet("database")}>
                        <Plus className="h-4 w-4" />
                        Добавить базу данных
                      </Button>
                      <Button variant="outline" onClick={() => openCreateConnectorSheet("llm")}>
                        <Plus className="h-4 w-4" />
                        Добавить LLM
                      </Button>
                      <Button variant="outline" onClick={() => openCreateConnectorSheet("documentation")}>
                        <Plus className="h-4 w-4" />
                        Добавить документацию
                      </Button>
                      <Button variant="outline" onClick={() => openCreateConnectorSheet("gitlab")}>
                        <Plus className="h-4 w-4" />
                        Добавить GitLab
                      </Button>
                      <Button variant="outline" onClick={() => openCreateConnectorSheet("mcp")}>
                        <Plus className="h-4 w-4" />
                        Добавить MCP
                      </Button>
                      <Button variant="outline" onClick={() => openCreateConnectorSheet("airflow")}>
                        <Plus className="h-4 w-4" />
                        Добавить Airflow
                      </Button>
                      <Button variant="outline" onClick={() => openCreateConnectorSheet("kubernetes")}>
                        <Plus className="h-4 w-4" />
                        Добавить K8s
                      </Button>
                      <Button variant="outline" onClick={() => openCreateConnectorSheet("victoriametrics")}>
                        <Plus className="h-4 w-4" />
                        Добавить VM
                      </Button>
                    </>
                  }
                >
                  <Card className="border-border bg-surface/[0.92]">
                    <CardContent className="grid gap-4 p-5 lg:grid-cols-[minmax(260px,1fr)_auto] lg:items-center">
                      <div className="relative">
                        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
                        <Input
                          value={setupSearch}
                          onChange={(event) => setSetupSearch(event.target.value)}
                          placeholder="Поиск по названию подключения, хосту, модели, URL или сервису"
                          className="pl-9"
                        />
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {(["all", "ready", "attention"] as SetupStatusFilter[]).map((filter) => (
                          <button
                            key={filter}
                            type="button"
                            onClick={() => setSetupStatus(filter)}
                            className={`rounded-full border px-3 py-2 text-sm transition ${
                              setupStatus === filter
                                ? "border-accent/40 bg-accent/[0.16] text-primary"
                                : "border-border-strong bg-surface text-secondary hover:border-border-strong hover:text-primary"
                            }`}
                          >
                            {SETUP_STATUS_FILTER_LABELS[filter]}
                          </button>
                        ))}
                      </div>
                    </CardContent>
                  </Card>

                  <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(240px,1fr))]">
                    <ConnectorTypeSummaryCard
                      title="База данных"
                      count={databaseConnectors.length}
                      readyCount={databaseConnectors.filter((item) => getConnectorHealth(item, "database") === "ready").length}
                      icon={<Database className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("database")}
                    />
                    <ConnectorTypeSummaryCard
                      title="LLM"
                      count={llmConnectors.length}
                      readyCount={llmConnectors.filter((item) => getConnectorHealth(item, "llm") === "ready").length}
                      icon={<Server className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("llm")}
                    />
                    <ConnectorTypeSummaryCard
                      title="Документация"
                      count={documentationConnectors.length}
                      readyCount={documentationConnectors.filter((item) => getConnectorHealth(item, "documentation") === "ready").length}
                      icon={<BookText className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("documentation")}
                    />
                    <ConnectorTypeSummaryCard
                      title="GitLab"
                      count={gitlabConnectors.length}
                      readyCount={gitlabConnectors.filter((item) => getConnectorHealth(item, "gitlab") === "ready").length}
                      icon={<BookText className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("gitlab")}
                    />
                    <ConnectorTypeSummaryCard
                      title="MCP"
                      count={mcpConnectors.length}
                      readyCount={mcpConnectors.filter((item) => getConnectorHealth(item, "mcp") === "ready").length}
                      icon={<Workflow className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("mcp")}
                    />
                    <ConnectorTypeSummaryCard
                      title="Airflow"
                      count={airflowConnectors.length}
                      readyCount={airflowConnectors.filter((item) => getConnectorHealth(item, "airflow") === "ready").length}
                      icon={<Workflow className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("airflow")}
                    />
                    <ConnectorTypeSummaryCard
                      title="Kubernetes"
                      count={kubernetesConnectors.length}
                      readyCount={kubernetesConnectors.filter((item) => getConnectorHealth(item, "kubernetes") === "ready").length}
                      icon={<Server className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("kubernetes")}
                    />
                    <ConnectorTypeSummaryCard
                      title="VictoriaMetrics"
                      count={victoriametricsConnectors.length}
                      readyCount={victoriametricsConnectors.filter((item) => getConnectorHealth(item, "victoriametrics") === "ready").length}
                      icon={<Clock3 className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("victoriametrics")}
                    />
                    <ConnectorTypeSummaryCard
                      title="Tag"
                      count={mattermostConnectors.length}
                      readyCount={mattermostConnectors.filter((item) => getConnectorHealth(item, "mattermost") === "ready").length}
                      icon={<MessageSquare className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("mattermost")}
                    />
                  </div>

                  <div className="grid gap-4">
                    {(
                      [
                        ["database", filteredConnectorCollections.database],
                        ["llm", filteredConnectorCollections.llm],
                        ["documentation", filteredConnectorCollections.documentation],
                        ["gitlab", filteredConnectorCollections.gitlab],
                        ["mcp", filteredConnectorCollections.mcp],
                        ["airflow", filteredConnectorCollections.airflow],
                        ["kubernetes", filteredConnectorCollections.kubernetes],
                        ["victoriametrics", filteredConnectorCollections.victoriametrics],
                        ["mattermost", filteredConnectorCollections.mattermost],
                      ] as [ConnectorType, AnyConnectorItem[]][]
                    ).map(([type, items]) => (
                      <InfoPanel
                        key={type}
                        title={`Подключения: ${getConnectorTypeLabel(type)}`}
                        description={`${items.length} записей соответствуют фильтрам`}
                      >
                        {items.length > 0 ? (
                          <LayoutGroup>
                            <div className="grid gap-4 xl:grid-cols-2">
                              {items.map((connector) => (
                                <motion.div key={connector.id} layout>
                                  <ConnectorCard
                                    connector={connector}
                                    type={type}
                                    statusPending={connectorStatusPendingId === connector.id}
                                    onToggleActive={(isActive) =>
                                      void handleConnectorActiveToggle(type, connector, isActive)
                                    }
                                    onEdit={() => openEditConnectorSheet(type, connector)}
                                    onDelete={() =>
                                      setDeleteIntent({
                                        kind: "connector",
                                        id: connector.id,
                                        connectorType: type,
                                        title: connector.name,
                                      })
                                    }
                                    onSync={
                                      type === "documentation"
                                        ? () => void handleDocumentationSync(connector as DocumentationConnectorItem)
                                        : type === "mcp"
                                          ? () => void handleMcpSync(connector as McpConnectorItem)
                                        : undefined
                                    }
                                    syncPending={documentationSyncPendingId === connector.id}
                                  />
                                </motion.div>
                              ))}
                            </div>
                          </LayoutGroup>
                        ) : (
                          <EmptyState
                            icon={connectorTypeIcon(type)}
                            title={`Нет подключений: ${getConnectorTypeLabel(type)}`}
                            description="Либо поиск слишком узкий, либо подключений нет"
                            action={
                              <Button size="sm" onClick={() => openCreateConnectorSheet(type)}>
                                Добавить подключение
                              </Button>
                            }
                          />
                        )}
                      </InfoPanel>
                    ))}
                  </div>
                </SectionShell>
              ) : null}

              {activeSection === "governance" ? (
                <SectionShell
                  eyebrow="Управление"
                  title="Что разрешено запускать аналитикам"
                  description="Официальные сценарии и навыки рабочего пространства работают вместе. Первые задают процесс, вторые — правила выполнения"
                  actions={
                    <>
                      <Button onClick={openCreateAgentSheet}>
                        <Plus className="h-4 w-4" />
                        Добавить агента
                      </Button>
                      <Link href="/scenarios/new?scope=official" className={buttonVariants({ variant: "outline" })}>
                        <Plus className="h-4 w-4" />
                        Добавить официальный сценарий
                      </Link>
                      <Button variant="outline" onClick={openCreateSkillSheet}>
                        <Plus className="h-4 w-4" />
                        Добавить навык
                      </Button>
                    </>
                  }
                >
                  <InfoPanel
                    title="Агенты"
                    description="Профили чата для разных пользователей со списками разрешённых подключений и дополнительными подсказками промпта"
                  >
                    {agents.length > 0 ? (
                      <div className="grid gap-3 lg:grid-cols-2">
                        {agents.map((agent) => (
                          <AgentCard
                            key={agent.id}
                            agent={agent}
                            onEdit={() => openEditAgentSheet(agent)}
                            onDelete={() => setDeleteIntent({ kind: "agent", agent })}
                          />
                        ))}
                      </div>
                    ) : (
                      <EmptyState
                        icon={<Sparkles className="h-5 w-5" />}
                        title="Нет агентов"
                        description="Создайте первый управляемый профиль чата"
                        action={
                          <Button size="sm" onClick={openCreateAgentSheet}>
                            Добавить агента
                          </Button>
                        }
                      />
                    )}
                  </InfoPanel>

                  <div className="grid gap-4 xl:grid-cols-2">
                    <InfoPanel
                      title="Официальные сценарии"
                      description="Опубликованные процессы, видимые аналитикам, с ожидаемым поведением"
                    >
                      {officialScenarios.length > 0 ? (
                        <div className="grid gap-4">
                          {officialScenarios.map((scenario) => (
                            <ScenarioCard
                              key={scenario.id}
                              scenario={scenario}
                              onEdit={() => {
                                window.location.assign(`/scenarios/${scenario.id}`);
                              }}
                              onArchive={() => setDeleteIntent({ kind: "scenario", scenario })}
                            />
                          ))}
                        </div>
                      ) : (
                        <EmptyState
                          icon={<Workflow className="h-5 w-5" />}
                          title="Официальных сценариев нет"
                          description="Без доверенных процессов у аналитиков управление остаётся только на бумаге"
                          action={
                            <Link href="/scenarios/new?scope=official" className={buttonVariants({ size: "sm" })}>
                              Создать сценарий
                            </Link>
                          }
                        />
                      )}
                    </InfoPanel>

                    <InfoPanel
                      title="Навыки рабочего пространства"
                      description="Playbooks оптимизации и рекомендации по настройке, привязанные к выполнению рабочего пространства"
                    >
                      {workspaceSkills.length > 0 ? (
                        <div className="grid max-h-[min(70vh,48rem)] gap-4 overflow-y-auto overscroll-contain pr-1 [scrollbar-gutter:stable]">
                          {workspaceSkills.map((skill) => (
                            <SkillCard
                              key={skill.id}
                              skill={skill}
                              onEdit={() => openEditSkillSheet(skill)}
                              onDelete={() =>
                                setDeleteIntent({
                                  kind: "skill",
                                  id: skill.id,
                                  title: skill.name,
                                })
                              }
                            />
                          ))}
                        </div>
                      ) : (
                        <EmptyState
                          icon={<BookText className="h-5 w-5" />}
                          title="Навыков рабочего пространства нет"
                          description="Правил выполнения пока нет"
                          action={
                            <Button size="sm" onClick={openCreateSkillSheet}>
                              Добавить навык
                            </Button>
                          }
                        />
                      )}
                    </InfoPanel>
                  </div>
                </SectionShell>
              ) : null}

              {activeSection === "access" ? (
                <SectionShell
                  eyebrow="Доступ"
                  title="Локальные учётные записи и роли"
                  description="Создавайте локальных пользователей и проверяйте права без поиска по карточкам"
                >
                  <div className="grid gap-4">
                    <InfoPanel
                      title="Создание локального пользователя"
                      description="Роль по умолчанию — аналитик. Повышайте осознанно"
                    >
                      <form
                        onSubmit={(event) => {
                          event.preventDefault();
                          void handleCreateLocalUser(new FormData(event.currentTarget), event.currentTarget);
                        }}
                        className="grid gap-3 lg:grid-cols-[minmax(160px,1fr)_minmax(180px,1fr)_160px_minmax(180px,1fr)_auto] lg:items-end"
                      >
                        <Input data-autofocus label="Логин" name="login" placeholder="new-analyst" required />
                        <Input
                          label="Пароль"
                          type="password"
                          name="password"
                          minLength={8}
                          placeholder="strong-pass-123"
                          required
                        />
                        <label>
                          <span className="mb-2 block text-sm font-medium text-primary">Роль</span>
                          <select
                            name="role"
                            defaultValue="analyst"
                            className="flex h-10 w-full rounded-lg border border-control-border bg-surface px-3 py-2 text-sm text-primary outline-none transition focus:border-transparent focus:ring-2 focus:ring-accent"
                          >
                            <option value="analyst">аналитик</option>
                            <option value="admin">администратор</option>
                          </select>
                        </label>
                        <Input label="Отображаемое имя" name="display_name" placeholder="Platform Admin" />

                        {userError ? (
                          <div className="lg:col-span-5">
                            <Alert variant="danger" title="Ошибка создания" description={userError} />
                          </div>
                        ) : null}

                        <div className="flex items-center justify-end lg:justify-start">
                          <Button type="submit" isLoading={userSaving} className="w-full lg:w-auto">
                            Создать пользователя
                          </Button>
                        </div>
                      </form>
                    </InfoPanel>

                    <InfoPanel
                      title="Текущие пользователи"
                      description="Удаление применяется сразу, но откатывается, если бэкенд отклонит запрос"
                    >
                      {localUsers.length > 0 ? (
                        <LocalUsersTable
                          users={localUsers}
                          currentUserId={currentUserId}
                          onConfigureRateLimit={(user) => {
                            setRateLimitError(null);
                            setRateLimitUser(user);
                          }}
                          onDelete={(user) => setDeleteIntent({ kind: "user", user })}
                          onCurrentUserDelete={() => setUserError("Текущего пользователя нельзя удалить")}
                        />
                      ) : (
                        <EmptyState
                          icon={<Users className="h-5 w-5" />}
                          title="Пользователей нет"
                          description="В локальной аутентификации пока нет учётных записей"
                        />
                      )}
                    </InfoPanel>
                  </div>
                </SectionShell>
              ) : null}

              {activeSection === "review" ? (
                <SectionShell
                  eyebrow="Проверка"
                  title="Очередь проверки инсайтов"
                  description="Очередь слева, детали справа, подтверждение или отклонение на одном экране"
                >
                  {reviewError ? (
                    <Alert variant="danger" title="Не удалось выполнить действие проверки" description={reviewError} />
                  ) : null}

                  {pendingInsights.length > 0 && selectedReview ? (
                    <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
                      <InfoPanel
                        title="Очередь"
                        description={`Ожидает проверки: ${pendingInsights.length} ${pluralize(pendingInsights.length, ["инсайт", "инсайта", "инсайтов"])}`}
                      >
                        <LayoutGroup>
                          <div className="grid gap-3">
                            {pendingInsights.map((item) => {
                              const isSelected = selectedReview.id === item.id;
                              return (
                                <motion.button
                                  key={item.id}
                                  layout
                                  type="button"
                                  onClick={() => setSelectedReviewId(item.id)}
                                  className={`rounded-[22px] border px-4 py-4 text-left transition ${
                                    isSelected
                                      ? "border-accent bg-elevated"
                                      : "border-border-strong bg-surface hover:border-border-strong hover:bg-surface"
                                  }`}
                                >
                                  <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                      <p className="truncate text-sm font-medium text-primary">{item.summary}</p>
                                      <p className="mt-1 line-clamp-2 text-sm leading-6 text-secondary">
                                        {item.answer_excerpt}
                                      </p>
                                    </div>
                                    <div className="flex flex-col items-end gap-2">
                                      <SectionBadge tone={item.llm_confidence >= 0.8 ? "warning" : "primary"}>
                                        {item.llm_confidence.toFixed(2)}
                                      </SectionBadge>
                                      <ReviewTag>{item.likes_count} отметок</ReviewTag>
                                    </div>
                                  </div>
                                </motion.button>
                              );
                            })}
                          </div>
                        </LayoutGroup>
                      </InfoPanel>

                      <InfoPanel
                        title={selectedReview.summary}
                        description={`Создано ${formatDateTime(selectedReview.created_at)} • уверенность ${selectedReview.llm_confidence.toFixed(2)}`}
                      >
                        <div className="grid gap-5">
                          <div className="grid gap-4 lg:grid-cols-3">
                            <SummaryStat label="Отметки" value={selectedReview.likes_count} />
                            <SummaryStat label="Таблицы" value={selectedReview.context_tables.length} />
                            <SummaryStat label="Источники" value={selectedReview.metadata_sources.length} />
                          </div>

                          <DetailBlock title="Обоснование" content={selectedReview.llm_reason} />
                          <DetailBlock title="Фрагмент ответа" content={selectedReview.answer_excerpt} />
                          {selectedReview.generated_sql ? (
                            <DetailCodeBlock title="Сгенерированный SQL" content={selectedReview.generated_sql} />
                          ) : null}
                          {selectedReview.article_markdown ? (
                            <DetailCodeBlock title="Черновик статьи" content={selectedReview.article_markdown} />
                          ) : null}

                          <div className="grid gap-4 lg:grid-cols-2">
                            <TagList title="Таблицы контекста" items={selectedReview.context_tables} />
                            <TagList title="Источники метаданных" items={selectedReview.metadata_sources} />
                            <TagList title="Ключевые слова" items={selectedReview.keywords} />
                            <InfoPanel title="Связи">
                              <div className="grid gap-3 text-sm text-primary">
                                <SummaryRow label="Диалог" value={selectedReview.conversation_id} monospace />
                                <SummaryRow label="Сообщение" value={selectedReview.message_id} monospace />
                                <SummaryRow label="Исполнение" value={selectedReview.execution_id ?? "—"} monospace />
                              </div>
                            </InfoPanel>
                          </div>

                          <div className="flex flex-wrap justify-end gap-3 border-t border-border pt-4">
                            <Button
                              variant="danger"
                              onClick={() => void handleReview(selectedReview.id, "reject")}
                              disabled={reviewPendingId === selectedReview.id}
                            >
                              <XCircle className="h-4 w-4" />
                              Отклонить
                            </Button>
                            <Button
                              variant="success"
                              onClick={() => void handleReview(selectedReview.id, "approve")}
                              disabled={reviewPendingId === selectedReview.id}
                            >
                              <CheckCircle2 className="h-4 w-4" />
                              Подтвердить
                            </Button>
                          </div>
                        </div>
                      </InfoPanel>
                    </div>
                  ) : (
                    <EmptyState
                      icon={<CheckCircle2 className="h-5 w-5" />}
                      title="Очередь пуста"
                      description="ООжидающих инсайтов нет. Либо качество под контролем, либо пайплайн бездействует"
                    />
                  )}
                </SectionShell>
              ) : null}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>

      <AgentSheet
        open={agentSheetOpen}
        onOpenChange={(open) => {
          if (!agentSaving) {
            setAgentSheetOpen(open);
            if (!open) {
              setEditingAgent(null);
              setAgentError(null);
            }
          }
        }}
        editingAgent={editingAgent}
        sessionNonce={agentSessionNonce}
        databaseConnectors={databaseConnectors}
        llmConnectors={llmConnectors}
        documentationConnectors={documentationConnectors}
        gitlabConnectors={gitlabConnectors}
        mcpConnectors={mcpConnectors}
        airflowConnectors={airflowConnectors}
        kubernetesConnectors={kubernetesConnectors}
        victoriametricsConnectors={victoriametricsConnectors}
        workspaceSkills={workspaceSkills}
        onSubmit={handleAgentSubmit}
        saving={agentSaving}
        error={agentError}
      />

      <ConnectorSheet
        open={connectorSheetOpen}
        onOpenChange={(open) => {
          if (!connectorSaving) {
            setConnectorSheetOpen(open);
            if (!open) {
              setEditingConnector(null);
              setConnectorError(null);
            }
          }
        }}
        connectorType={connectorSheetType}
        editingConnector={editingConnector}
        sessionNonce={connectorSessionNonce}
        onSubmit={handleConnectorSubmit}
        saving={connectorSaving}
        error={connectorError}
        agents={agents}
      />

      <SkillSheet
        open={skillSheetOpen}
        onOpenChange={(open) => {
          if (!skillSaving) {
            setSkillSheetOpen(open);
            if (!open) {
              setEditingSkill(null);
              setSkillError(null);
            }
          }
        }}
        editingSkill={editingSkill}
        sessionNonce={skillSessionNonce}
        onSubmit={handleSkillSubmit}
        saving={skillSaving}
        error={skillError}
      />

      <ConfirmDialog
        open={deleteIntent != null}
        onOpenChange={(open) => {
          if (!open && !deletePending) {
            setDeleteIntent(null);
          }
        }}
        title={buildDeleteTitle(deleteIntent)}
        description={buildDeleteDescription(deleteIntent, currentUserId)}
        confirmLabel={deleteIntent?.kind === "scenario" ? "В архив" : "Удалить"}
        onConfirm={() => void confirmDeleteIntent()}
        isPending={deletePending}
      />

      <UserRateLimitDialog
        key={`${rateLimitUser?.id ?? "closed"}:${rateLimitUser?.api_rate_limit_per_hour ?? "none"}`}
        user={rateLimitUser}
        open={rateLimitUser != null}
        busy={rateLimitSaving}
        error={rateLimitError}
        onOpenChange={(open) => {
          if (!open) {
            setRateLimitUser(null);
            setRateLimitError(null);
          }
        }}
        onSave={handleSaveUserRateLimit}
        onReset={handleResetUserRateLimit}
      />
    </>
  );
}

function buildDeleteTitle(intent: DeleteIntent | null) {
  if (!intent) {
    return "Удаление объекта";
  }

  if (intent.kind === "connector") {
    return `Удалить ${intent.title}?`;
  }
  if (intent.kind === "skill") {
    return `Удалить ${intent.title}?`;
  }
  if (intent.kind === "user") {
    return `Удалить пользователя ${intent.user.login}?`;
  }
  if (intent.kind === "agent") {
    return `Удалить ${intent.agent.title}?`;
  }
  return `Отправить в архив ${intent.scenario.title}?`;
}

function buildDeleteDescription(intent: DeleteIntent | null, currentUserId: string) {
  if (!intent) {
    return "Это действие нельзя отменить";
  }

  if (intent.kind === "connector") {
    return "Рантайм сразу лишится этой конфигурации подключения. Если от неё зависит другой объект, вам придётся разобраться с последствиями самостоятельно.";
  }
  if (intent.kind === "skill") {
    return "Рабочее пространство перестанет использовать этот снимок навыка для оптимизации выполнения";
  }
  if (intent.kind === "user") {
    return intent.user.id === currentUserId
      ? "Текущего пользователя нельзя удалить"
      : "Учётная запись сразу потеряет доступ";
  }
  if (intent.kind === "agent") {
    return "Агент исчезнет из выбора в чате, но история существующих диалогов сохранит его авторство";
  }
  return "Сценарий перейдёт в архив и покинет активный список управления";
}

function SummaryRow({
  label,
  value,
  monospace = false,
}: {
  label: string;
  value: React.ReactNode;
  monospace?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-2xl border border-border bg-surface px-3 py-3">
      <span className="text-sm text-secondary">{label}</span>
      <span className={`min-w-0 break-words text-right text-sm font-medium text-primary ${monospace ? "font-mono text-[13px]" : ""}`}>
        {value}
      </span>
    </div>
  );
}

function SummaryStat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-[22px] border border-border bg-surface px-4 py-4">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted">{label}</p>
      <div className="mt-2 text-2xl font-semibold tracking-[-0.04em] text-primary">{value}</div>
    </div>
  );
}

function CompactSummaryStat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="inline-flex min-w-[120px] items-center justify-between gap-3 rounded-lg border border-border bg-surface px-3 py-2">
      <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">{label}</span>
      <span className="text-sm font-semibold text-primary">{value}</span>
    </div>
  );
}

function DetailBlock({ title, content }: { title: string; content: string }) {
  return (
    <div className="grid gap-2">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted">{title}</p>
      <div className="rounded-[22px] border border-border bg-surface px-4 py-4 text-sm leading-7 text-primary">
        {content}
      </div>
    </div>
  );
}

function DetailCodeBlock({ title, content }: { title: string; content: string }) {
  return (
    <div className="grid gap-2">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted">{title}</p>
      <pre className="overflow-x-auto rounded-[22px] border border-border bg-surface px-4 py-4 text-xs leading-6 text-primary">
        {content}
      </pre>
    </div>
  );
}

function TagList({ title, items }: { title: string; items: string[] }) {
  return (
    <InfoPanel title={title}>
      {items.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {items.map((item) => (
            <ReviewTag key={item}>{item}</ReviewTag>
          ))}
        </div>
      ) : (
        <p className="text-sm text-secondary">Нет данных</p>
      )}
    </InfoPanel>
  );
}

function LocalUsersTable({
  users,
  currentUserId,
  onConfigureRateLimit,
  onDelete,
  onCurrentUserDelete,
}: {
  users: LocalUserItem[];
  currentUserId: string;
  onConfigureRateLimit: (user: LocalUserItem) => void;
  onDelete: (user: LocalUserItem) => void;
  onCurrentUserDelete: () => void;
}) {
  return (
    <div className="overflow-hidden rounded-2xl border border-border">
      <div className="overflow-x-auto">
        <table className="min-w-[760px] w-full border-collapse text-left text-sm">
          <thead className="bg-surface text-xs uppercase tracking-[0.14em] text-muted">
            <tr>
              <th className="px-4 py-3 font-semibold">Пользователь</th>
              <th className="px-4 py-3 font-semibold">Роль</th>
              <th className="px-4 py-3 font-semibold">Статус</th>
              <th className="px-4 py-3 font-semibold">Лимит API</th>
              <th className="px-4 py-3 font-semibold">Провайдер</th>
              <th className="px-4 py-3 font-semibold">Субъект</th>
              <th className="px-4 py-3 font-semibold">Создан</th>
              <th className="px-4 py-3 text-right font-semibold">Действия</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border bg-surface">
            {users.map((user) => {
              const isCurrentUser = user.id === currentUserId;
              return (
                <tr key={user.id} className="transition hover:bg-surface">
                  <td className="px-4 py-3">
                    <div className="max-w-[220px]">
                      <p className="truncate font-medium text-primary">{user.login}</p>
                      <p className="mt-1 truncate text-xs text-secondary">{user.display_name ?? "-"}</p>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <SectionBadge tone={user.role === "admin" ? "warning" : "primary"}>{user.role}</SectionBadge>
                  </td>
                  <td className="px-4 py-3">
                    <SectionBadge tone={user.is_active ? "success" : "danger"}>
                      {user.is_active ? "активен" : "отключён"}
                    </SectionBadge>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-primary">
                    {user.api_rate_limit_per_hour == null
                      ? "Без ограничений"
                      : `${user.api_rate_limit_per_hour} / час`}
                  </td>
                  <td className="px-4 py-3 text-primary">{user.auth_provider}</td>
                  <td className="px-4 py-3">
                    <code className="block max-w-[220px] truncate rounded-md bg-page px-2 py-1 text-xs text-primary">
                      {user.auth_subject}
                    </code>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-primary">
                    {formatShortDate(user.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        aria-label={`Настроить лимит API для ${user.login}`}
                        onClick={() => onConfigureRateLimit(user)}
                      >
                        <Clock3 className="h-4 w-4" />
                        Лимит
                      </Button>
                      <button
                        type="button"
                        title={isCurrentUser ? "Текущего пользователя нельзя удалить" : "Удалить"}
                        disabled={isCurrentUser}
                        onClick={() => {
                          if (isCurrentUser) {
                            onCurrentUserDelete();
                            return;
                          }
                          onDelete(user);
                        }}
                        className="inline-flex h-9 items-center justify-center rounded-lg border border-danger-border bg-danger-subtle px-3 text-sm font-medium text-danger transition hover:bg-danger-subtle disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Trash2 className="mr-2 h-4 w-4" />
                        Удалить
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ConnectorTypeSummaryCard({
  title,
  count,
  readyCount,
  icon,
  onAdd,
}: {
  title: string;
  count: number;
  readyCount: number;
  icon: React.ReactNode;
  onAdd: () => void;
}) {
  return (
    <Card className="min-w-0 border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.96),rgb(var(--color-surface)/0.98))]">
      <CardContent className="grid gap-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-border-strong bg-surface text-accent">
              {icon}
            </div>
            <div className="min-w-0">
              <p className="break-words text-base font-medium text-primary">{title}</p>
              <p className="text-sm text-secondary">{readyCount}/{count} готово</p>
            </div>
          </div>
          <SectionBadge tone={count > 0 && readyCount === count ? "success" : count > 0 ? "warning" : "danger"}>
            {count}
          </SectionBadge>
        </div>
        <Button size="sm" variant="outline" onClick={onAdd} className="w-full min-w-0">
          <Plus className="h-4 w-4" />
          <span className="min-w-0 truncate">Добавить подключение</span>
        </Button>
      </CardContent>
    </Card>
  );
}

function ConnectorCard({
  connector,
  type,
  statusPending,
  onToggleActive,
  onEdit,
  onDelete,
  onSync,
  syncPending = false,
}: {
  connector: AnyConnectorItem;
  type: ConnectorType;
  statusPending: boolean;
  onToggleActive: (isActive: boolean) => void;
  onEdit: () => void;
  onDelete: () => void;
  onSync?: () => void;
  syncPending?: boolean;
}) {
  const health = getConnectorHealth(connector, type);
  const healthTone = connector.is_active ? (health === "ready" ? "success" : "warning") : "danger";

  return (
    <Card className="border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.96),rgb(var(--color-surface)/0.98))]">
      <CardContent className="grid gap-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-base font-medium text-primary">{connector.name}</p>
            <p className="mt-1 text-sm text-secondary">{getConnectorSubtitle(connector, type)}</p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <SectionBadge tone={healthTone}>{connector.is_active ? health : "неактивно"}</SectionBadge>
            <Switch
              aria-label={`${connector.name}: активность`}
              checked={connector.is_active}
              disabled={statusPending}
              onChange={(event) => onToggleActive(event.currentTarget.checked)}
              label={connector.is_active ? "Активно" : "Неактивно"}
            />
          </div>
        </div>
        <div className="grid gap-3 text-sm text-primary">
          {getConnectorDetailRows(connector, type).map((row) => (
            <SummaryRow key={row.label} label={row.label} value={row.value} monospace={row.monospace} />
          ))}
        </div>
        <div className="flex flex-wrap justify-between gap-3 border-t border-border pt-4">
          <span className="text-xs uppercase tracking-[0.16em] text-muted">
            Обновлено {formatShortDate(connector.updated_at)}
          </span>
          <div className="flex gap-2">
            {onSync ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={onSync}
                disabled={syncPending}
                className="border border-border-strong bg-surface text-primary hover:bg-elevated"
              >
                <Clock3 className="h-4 w-4" />
                Синхронизировать
              </Button>
            ) : null}
            <Button size="sm" variant="ghost" onClick={onEdit} className="border border-border-strong bg-surface text-primary hover:bg-elevated">
              <Edit className="h-4 w-4" />
              Редактировать
            </Button>
            <Button size="sm" variant="danger" onClick={onDelete}>
              <Trash2 className="h-4 w-4" />
              Удалить
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ScenarioCard({
  scenario,
  onEdit,
  onArchive,
}: {
  scenario: ScenarioDefinition;
  onEdit: () => void;
  onArchive: () => void;
}) {
  return (
    <Card className="border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.96),rgb(var(--color-surface)/0.98))]">
      <CardContent className="grid gap-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-base font-medium text-primary">{scenario.title}</p>
            <p className="mt-1 text-sm text-secondary">{scenario.summary}</p>
          </div>
          <SectionBadge tone={scenario.status === "published" ? "success" : "warning"}>
            {scenario.status}
          </SectionBadge>
        </div>
        <div className="grid gap-3 text-sm text-primary">
          <SummaryRow label="Идентификатор" value={scenario.slug} monospace />
          <SummaryRow label="Версия" value={`v${scenario.version}`} />
          <SummaryRow label="Обновлено" value={formatShortDate(scenario.updated_at)} />
        </div>
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button size="sm" variant="ghost" onClick={onEdit} className="border border-border-strong bg-surface text-primary hover:bg-elevated">
            <Edit className="h-4 w-4" />
            Редактировать
          </Button>
          <Button size="sm" variant="danger" onClick={onArchive}>
            <Trash2 className="h-4 w-4" />
            В архив
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function SkillCard({
  skill,
  onEdit,
  onDelete,
}: {
  skill: WorkspaceSkillItem;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <Card className="border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.96),rgb(var(--color-surface)/0.98))]">
      <CardContent className="grid gap-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-base font-medium text-primary">{skill.name}</p>
            <p className="mt-1 text-sm text-secondary">{skill.summary ?? "Описание ещё не получено"}</p>
          </div>
          <div className="flex flex-col gap-2">
            <SectionBadge tone={skill.status === "active" ? "success" : "warning"}>{skill.status}</SectionBadge>
            <SectionBadge tone={skill.validation_status === "valid" ? "success" : skill.validation_status === "invalid" ? "danger" : "warning"}>
              {skill.validation_status}
            </SectionBadge>
          </div>
        </div>
        <div className="grid gap-3 text-sm text-primary">
          <SummaryRow label="Приоритет" value={skill.priority} />
          <SummaryRow label="Назначение" value={skill.capability_family} />
          <SummaryRow label="Область" value={skill.engine_scope} />
          <SummaryRow
            label="Источник"
            value={skill.source_kind === "manual_markdown" ? "ручной текст" : skill.source_url}
            monospace={skill.source_kind !== "manual_markdown"}
          />
          {skill.capability_family === "chat_instruction" ? (
            <SummaryRow
              label="Ассистент по умолчанию"
              value={skill.visible_to_default_assistant ? "виден" : "скрыт"}
            />
          ) : null}
          <SummaryRow label="Проверен" value={formatDateTime(skill.last_validated_at)} />
        </div>
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button size="sm" variant="ghost" onClick={onEdit} className="border border-border-strong bg-surface text-primary hover:bg-elevated">
            <Edit className="h-4 w-4" />
            Редактировать
          </Button>
          <Button size="sm" variant="danger" onClick={onDelete}>
            <Trash2 className="h-4 w-4" />
            Удалить
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function AgentCard({
  agent,
  onEdit,
  onDelete,
}: {
  agent: AgentProfile;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <Card className="border-border-strong bg-surface">
      <CardContent className="grid gap-4 p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-base font-medium text-primary">{agent.title}</p>
            <p className="mt-1 text-sm text-secondary">{agent.description ?? agent.slug}</p>
          </div>
          <SectionBadge tone={agent.is_active ? "success" : "danger"}>
            {agent.is_active ? "активен" : "неактивен"}
          </SectionBadge>
        </div>
        <div className="grid gap-2 text-sm">
          <SummaryRow label="Звёзды" value={agent.star_count} />
          <SummaryRow label="LLM" value={agent.llm_connector_name ?? "по умолчанию"} />
          <SummaryRow label="Разрешённые БД" value={agent.allowed_database_connector_names.join(", ") || "все"} />
          <SummaryRow label="Разрешённые GitLab" value={agent.allowed_gitlab_connector_names.join(", ") || "запрещено"} />
          <SummaryRow label="Разрешённые MCP" value={agent.allowed_mcp_connector_names.join(", ") || "запрещено"} />
          <SummaryRow label="Разрешённые Airflow" value={agent.allowed_airflow_connector_names.join(", ") || "запрещено"} />
          <SummaryRow label="Разрешённые K8s" value={agent.allowed_kubernetes_connector_names.join(", ") || "запрещено"} />
          <SummaryRow
            label="Разрешённые VM"
            value={agent.allowed_victoriametrics_connector_names.join(", ") || "запрещено"}
          />
          <SummaryRow label="Навыки для чата" value={agent.allowed_workspace_skill_ids.length} />
          <SummaryRow label="Обновлено" value={formatShortDate(agent.updated_at)} />
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={onEdit}>
            <Edit className="h-4 w-4" />
            Редактировать
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={onDelete}>
            <Trash2 className="h-4 w-4" />
            Удалить
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function getConnectorSubtitle(connector: AnyConnectorItem, type: ConnectorType) {
  if (type === "database") {
    const item = connector as DatabaseConnectorItem;
    return `${item.connection.engine} • ${item.connection.host}:${item.connection.port}`;
  }
  if (type === "llm") {
    const item = connector as LlmConnectorItem;
    return `${item.connection.model} • ${item.connection.base_url}`;
  }
  if (type === "documentation") {
    const item = connector as DocumentationConnectorItem;
    return `${item.connection.provider} • ${item.connection.base_url}`;
  }
  if (type === "gitlab") {
    const item = connector as GitLabConnectorItem;
    return `только чтение • ${item.connection.base_url}`;
  }
  if (type === "mcp") {
    const item = connector as McpConnectorItem;
    return `${item.connection.provider} • ${item.connection.url}`;
  }
  if (type === "airflow") {
    const item = connector as AirflowConnectorItem;
    return `только чтение • ${item.connection.base_url}${item.connection.api_path}`;
  }
  if (type === "kubernetes") {
    const item = connector as KubernetesConnectorItem;
    return `только чтение • ${item.connection.base_url}`;
  }
  if (type === "victoriametrics") {
    const item = connector as VictoriaMetricsConnectorItem;
    return `${item.connection.templates.length} шаблонов • ${item.connection.base_url}`;
  }
  const item = connector as MattermostConnectorItem;
  return `${item.connection.bot_username ?? "имя пользователя по умолчанию"} • ${item.connection.base_url}`;
}

function getConnectorDetailRows(
  connector: AnyConnectorItem,
  type: ConnectorType
): Array<{ label: string; value: React.ReactNode; monospace?: boolean }> {
  if (type === "database") {
    const item = connector as DatabaseConnectorItem;
    return [
      { label: "Применять, когда", value: item.connection.usage_description ?? "—" },
      { label: "База данных", value: item.connection.database ?? item.connection.catalog ?? "—" },
      { label: "Схема", value: item.connection.schema ?? "—" },
      { label: "Безопасность", value: item.connection.secure ? "TLS включён" : "Без TLS" },
      { label: "Пароль", value: item.has_password ? "сохранён" : "не задан" },
    ];
  }
  if (type === "llm") {
    const item = connector as LlmConnectorItem;
    return [
      { label: "Стандартный агент", value: item.is_default ? "По умолчанию" : "Не по умолчанию" },
      { label: "Эндпоинт", value: item.connection.endpoint_path, monospace: true },
      { label: "TLS", value: item.connection.verify_tls ? "проверяется" : "отключено" },
      { label: "Ключи API", value: item.has_api_key ? `${item.api_key_count || 1} сохранено` : "не задан" },
      { label: "Температура", value: item.connection.temperature ?? "По умолчанию провайдера" },
      { label: "Top P", value: item.connection.top_p ?? "По умолчанию провайдера" },
      { label: "Уровень reasoning", value: item.connection.reasoning_effort ?? "По умолчанию провайдера" },
      { label: "Максимум токенов ответа", value: item.connection.max_tokens ?? "По умолчанию провайдера" },
      {
        label: "Параметр максимума токенов",
        value: item.connection.max_tokens_parameter ?? "max_tokens",
        monospace: true,
      },
      {
        label: "Контекстное окно",
        value:
          item.connection.extra_settings?.context_window_tokens ??
          item.connection.extra_settings?.context_window ??
          "—",
      },
      {
        label: "Протокол финального ответа",
        value: item.connection.extra_settings?.require_explicit_final_answer
          ? "Обязательный инструмент"
          : "По умолчанию провайдера",
      },
    ];
  }
  if (type === "documentation") {
    const item = connector as DocumentationConnectorItem;
    return [
      { label: "Применять, когда", value: item.connection.usage_description ?? "—" },
      {
        label: "Агент по умолчанию",
        value: item.visible_to_default_assistant ? "виден" : "скрыт",
      },
      { label: "Индекс", value: item.connection.index_url ?? "автоопределение", monospace: Boolean(item.connection.index_url) },
      { label: "Расписание", value: item.connection.sync_enabled ? `${item.connection.sync_interval_minutes} мин` : "отключено" },
      { label: "Последняя синхронизация", value: item.last_sync_at ? formatDateTime(item.last_sync_at) : "—" },
      { label: "Статус", value: item.last_sync_status ?? "ни разу" },
      { label: "Эмбеддинги", value: item.connection.embedding_model, monospace: true },
      { label: "Qdrant", value: item.connection.qdrant_collection_name, monospace: true },
      { label: "Ключ эмбеддингов", value: item.has_embedding_api_key ? "сохранён" : "не требуется" },
    ];
  }
  if (type === "gitlab") {
    const item = connector as GitLabConnectorItem;
    return [
      { label: "Применять, когда", value: item.connection.use_when },
      { label: "Проекты", value: item.connection.default_project_paths.join(", ") || "—", monospace: true },
      { label: "Группы", value: item.connection.default_group_paths.join(", ") || "—", monospace: true },
      { label: "Лимиты", value: `${item.connection.search_limit} поиск / ${item.connection.read_max_tokens} токенов на чтение` },
      { label: "Токен", value: item.has_api_token ? "сохранён" : "не задан" },
    ];
  }
  if (type === "mcp") {
    const item = connector as McpConnectorItem;
    const allowedToolNames = item.connection.allowed_tool_names ?? [];
    return [
      { label: "Применять, когда", value: item.connection.usage_description ?? "—" },
      {
        label: "Внешний MCP",
        value: item.visible_to_external_mcp ? "опубликован" : "скрыт",
      },
      { label: "Группы", value: item.connection.enabled_groups ?? "все" },
      {
        label: "Разрешённые методы",
        value: allowedToolNames.join(", ") || "все (без ограничений)",
        monospace: allowedToolNames.length > 0,
      },
      { label: "Префикс", value: item.connection.tool_name_prefix ?? item.name, monospace: true },
      { label: "Инструменты", value: item.tool_count },
      { label: "Секреты", value: item.has_secret_headers ? item.secret_header_names.join(", ") : "не заданы" },
      { label: "Последняя синхронизация", value: item.last_sync_at ? formatDateTime(item.last_sync_at) : "ни разу" },
      { label: "Статус синхронизации", value: item.last_sync_status ?? "ожидает" },
    ];
  }
  if (type === "airflow") {
    const item = connector as AirflowConnectorItem;
    return [
      { label: "Применять, когда", value: item.connection.use_when },
      { label: "Имя пользователя", value: item.connection.username },
      { label: "Путь API", value: item.connection.api_path, monospace: true },
      {
        label: "Лимиты",
        value: `${item.connection.dag_limit} DAG / ${item.connection.dag_run_limit} запусков / ${item.connection.task_limit} задач`,
      },
      { label: "Лимит лога", value: `${item.connection.log_max_chars} символов` },
      { label: "Пароль", value: item.has_password ? "сохранён" : "не задан" },
    ];
  }
  if (type === "kubernetes") {
    const item = connector as KubernetesConnectorItem;
    return [
      { label: "Применять, когда", value: item.connection.use_when },
      {
        label: "Агент по умолчанию",
        value: item.visible_to_default_assistant ? "виден" : "скрыт",
      },
      { label: "Пространства имён", value: item.connection.namespace_allowlist.join(", "), monospace: true },
      {
        label: "Лимиты",
        value: `${item.connection.default_limit} по умолчанию / ${item.connection.max_limit} максимум`,
      },
      { label: "Лимит лога", value: `${item.connection.log_max_chars} символов` },
      { label: "Токен", value: item.has_bearer_token ? "сохранён" : "не задан" },
    ];
  }
  if (type === "victoriametrics") {
    const item = connector as VictoriaMetricsConnectorItem;
    return [
      { label: "Применять, когда", value: item.connection.use_when },
      {
        label: "Агент по умолчанию",
        value: item.visible_to_default_assistant ? "виден" : "скрыт",
      },
      { label: "Аутентификация", value: item.connection.auth_type },
      {
        label: "Шаблоны",
        value: item.connection.templates.map((template) => template.template_id).join(", ") || "—",
        monospace: true,
      },
      {
        label: "Лимиты",
        value: `${item.connection.max_series} серий / ${item.connection.max_datapoints} точек`,
      },
      { label: "Токен", value: item.has_bearer_token ? "сохранён" : "не требуется" },
    ];
  }
  const item = connector as MattermostConnectorItem;
  return [
    { label: "Бот", value: item.connection.bot_username ?? "по умолчанию" },
    { label: "Прослушивание", value: item.connection.listen_enabled ? "включено" : "отключено" },
    { label: "Ответы с SQL", value: item.connection.send_sql_enabled ? "включено" : "отключено" },
    {
      label: "Фоновый режим",
      value: item.connection.ambient_listen_enabled
        ? `${item.connection.ambient_channel_ids.length} каналов`
        : "отключено",
    },
    { label: "TLS", value: item.connection.verify_tls ? "проверяется" : "отключено" },
    { label: "Токен", value: item.has_bot_token ? "сохранён" : "не задан" },
  ];
}

function connectorTypeIcon(type: ConnectorType) {
  if (type === "database") {
    return <Database className="h-5 w-5" />;
  }
  if (type === "llm") {
    return <Server className="h-5 w-5" />;
  }
  if (type === "gitlab") {
    return <GitBranch className="h-5 w-5" />;
  }
  if (type === "mcp") {
    return <Workflow className="h-5 w-5" />;
  }
  if (type === "airflow") {
    return <Workflow className="h-5 w-5" />;
  }
  if (type === "kubernetes") {
    return <Server className="h-5 w-5" />;
  }
  if (type === "victoriametrics") {
    return <Activity className="h-5 w-5" />;
  }
  if (type === "mattermost") {
    return <MessageSquare className="h-5 w-5" />;
  }
  return <BookText className="h-5 w-5" />;
}

function upsertById<T extends { id: string }>(items: T[], nextItem: T) {
  const next = items.filter((item) => item.id !== nextItem.id);
  return [nextItem, ...next];
}

function upsertLlmConnector(items: LlmConnectorItem[], nextItem: LlmConnectorItem) {
  const normalized = nextItem.is_default
    ? items.map((item) => ({ ...item, is_default: false }))
    : items;
  return upsertById(normalized, nextItem);
}
