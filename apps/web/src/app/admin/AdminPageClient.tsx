"use client";

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
  ArrowRight,
  BookText,
  CheckCircle2,
  Clock3,
  Database,
  Edit,
  Layers,
  MessageSquare,
  Plus,
  Search,
  Server,
  Shield,
  Sparkles,
  Trash2,
  UserRound,
  Users,
  Workflow,
  XCircle,
} from "lucide-react";

import { ConnectorSheet } from "./ConnectorSheet";
import { SkillSheet } from "./SkillSheet";
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
  ConnectorType,
  ConfluenceConnectorItem,
  DatabaseConnectorItem,
  JiraConnectorItem,
  LlmConnectorItem,
  LocalUserItem,
  MattermostConnectorItem,
  OpenMetadataConnectorItem,
  RagInsightReviewItem,
  WorkspaceSkillItem,
} from "./types";
import { ScenarioEditorModal } from "../components/scenario-editor-modal";
import {
  archiveScenario,
  createScenario,
  updateScenario,
  type SaveScenarioPayload,
  type ScenarioDefinition,
} from "@/features/scenarios/scenariosApi";

type DeleteIntent =
  | { kind: "connector"; id: string; connectorType: ConnectorType; title: string }
  | { kind: "skill"; id: string; title: string }
  | { kind: "user"; user: LocalUserItem }
  | { kind: "scenario"; scenario: ScenarioDefinition };

type SectionMeta = {
  id: AdminSectionId;
  label: string;
  description: string;
};

const SECTION_META: SectionMeta[] = [
  {
    id: "overview",
    label: "Overview",
    description: "Readiness, blockers, next moves",
  },
  {
    id: "setup",
    label: "Setup",
    description: "Connectors and runtime wiring",
  },
  {
    id: "governance",
    label: "Governance",
    description: "Official scenarios and skills",
  },
  {
    id: "access",
    label: "Access",
    description: "Local users and permissions",
  },
  {
    id: "review",
    label: "Review",
    description: "Pending RAG insight verification",
  },
];

function parseErrorText(text: string, fallbackStatus: number) {
  if (!text) {
    return `Request failed with status ${fallbackStatus}`;
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

  return `Request failed with status ${fallbackStatus}`;
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
        : type === "metadata"
          ? "/api/admin/openmetadata-connectors"
          : type === "confluence"
            ? "/api/admin/confluence-connectors"
            : type === "jira"
              ? "/api/admin/jira-connectors"
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
  settings: Record<string, string | number | boolean>,
  key: string,
  value: FormDataEntryValue | null
) {
  const parsed = String(value ?? "").trim();
  if (parsed) {
    settings[key] = parsed;
  }
}

function putNumberSetting(
  settings: Record<string, string | number | boolean>,
  key: string,
  value: FormDataEntryValue | null
) {
  const parsed = String(value ?? "").trim();
  if (parsed) {
    settings[key] = Number(parsed);
  }
}

function buildDatabaseExtraSettings(formData: FormData): Record<string, string | number | boolean> {
  const settings: Record<string, string | number | boolean> = {};
  putStringSetting(settings, "metadata_service", formData.get("metadata_service"));
  putStringSetting(settings, "metadata_database", formData.get("metadata_database"));
  putStringSetting(settings, "metadata_schema", formData.get("metadata_schema"));
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
      connection: {
        ...item.connection,
        api_key: null,
        extra_settings: item.connection.extra_settings ?? {},
      },
    };
  }
  if (type === "metadata") {
    const item = connector as OpenMetadataConnectorItem;
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
  if (type === "confluence") {
    const item = connector as ConfluenceConnectorItem;
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
  if (type === "jira") {
    const item = connector as JiraConnectorItem;
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
    primary: "bg-[#5da8ff]/16 text-[#b8d9ff] border-[#5da8ff]/28",
    success: "bg-[#38c67d]/16 text-[#8ae0b0] border-[#38c67d]/24",
    warning: "bg-[#f2b14b]/14 text-[#ffd386] border-[#f2b14b]/28",
    danger: "bg-[#ff6d7c]/16 text-[#ffb2bb] border-[#ff6d7c]/24",
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
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-[#6e84a8]">
            {eyebrow}
          </p>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-[#eef4ff]">
            {title}
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-[#8ea3c5]">{description}</p>
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
    primary: "border-[#295289] bg-[radial-gradient(circle_at_top_right,rgba(93,168,255,0.16),transparent_34%),linear-gradient(180deg,rgba(9,15,27,0.98),rgba(8,12,22,0.98))] text-[#8fb9ff]",
    success: "border-[#214e3b] bg-[radial-gradient(circle_at_top_right,rgba(56,198,125,0.16),transparent_34%),linear-gradient(180deg,rgba(9,15,27,0.98),rgba(8,12,22,0.98))] text-[#7bdca5]",
    warning: "border-[#5b4720] bg-[radial-gradient(circle_at_top_right,rgba(242,177,75,0.16),transparent_34%),linear-gradient(180deg,rgba(9,15,27,0.98),rgba(8,12,22,0.98))] text-[#ffd178]",
    danger: "border-[#5b2730] bg-[radial-gradient(circle_at_top_right,rgba(255,109,124,0.16),transparent_34%),linear-gradient(180deg,rgba(9,15,27,0.98),rgba(8,12,22,0.98))] text-[#ffb2bb]",
  };

  return (
    <Card className={`border ${map[tone]} shadow-[0_18px_48px_rgba(0,0,0,0.28)]`}>
      <CardContent className="flex items-start gap-4 p-5">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-current/20 bg-current/10">
          {icon}
        </div>
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#6e84a8]">{title}</p>
          <div className="text-3xl font-semibold tracking-[-0.04em] text-[#eef4ff]">{value}</div>
          <p className="text-sm leading-6 text-[#8ea3c5]">{caption}</p>
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
    <Card className="min-w-0 overflow-hidden border-[#182438] bg-[linear-gradient(180deg,rgba(10,16,29,0.96),rgba(8,12,22,0.98))]">
      <CardHeader className="min-w-0 border-b border-[#182438]">
        <CardTitle className="min-w-0 break-words text-lg">{title}</CardTitle>
        {description ? <CardDescription className="min-w-0 break-words">{description}</CardDescription> : null}
      </CardHeader>
      <CardContent className="min-w-0 p-5">{children}</CardContent>
    </Card>
  );
}

function ReviewTag({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex rounded-full border border-[#263750] bg-[#0b1322] px-2.5 py-1 text-xs text-[#b5c7e3]">
      {children}
    </span>
  );
}

export function AdminPageClient({
  databaseConnectors: initialDb,
  llmConnectors: initialLlm,
  openMetadataConnectors: initialOm,
  confluenceConnectors: initialConfluence,
  jiraConnectors: initialJira,
  mattermostConnectors: initialMattermost,
  pendingInsights: initialPending,
  scenarios: initialScenarios,
  workspaceSkills: initialWorkspaceSkills,
  localUsers: initialLocalUsers,
  currentUserId,
  workspaceId,
}: AdminPageData) {
  const { addToast } = useToast();

  const [activeSection, setActiveSection] = useState<AdminSectionId>("overview");
  const [databaseConnectors, setDatabaseConnectors] = useState(initialDb);
  const [llmConnectors, setLlmConnectors] = useState(initialLlm);
  const [openMetadataConnectors, setOpenMetadataConnectors] = useState(initialOm);
  const [confluenceConnectors, setConfluenceConnectors] = useState(initialConfluence);
  const [jiraConnectors, setJiraConnectors] = useState(initialJira);
  const [mattermostConnectors, setMattermostConnectors] = useState(initialMattermost);
  const [pendingInsights, setPendingInsights] = useState(initialPending);
  const [scenarios, setScenarios] = useState(initialScenarios);
  const [workspaceSkills, setWorkspaceSkills] = useState(sortSkills(initialWorkspaceSkills));
  const [localUsers, setLocalUsers] = useState(sortUsers(initialLocalUsers));

  const [setupSearch, setSetupSearch] = useState("");
  const deferredSetupSearch = useDeferredValue(setupSearch);
  const [setupStatus, setSetupStatus] = useState<SetupStatusFilter>("all");
  const [connectorSheetOpen, setConnectorSheetOpen] = useState(false);
  const [connectorSheetType, setConnectorSheetType] = useState<ConnectorType | null>(null);
  const [editingConnector, setEditingConnector] = useState<AnyConnectorItem | null>(null);
  const [connectorSaving, setConnectorSaving] = useState(false);
  const [connectorStatusPendingId, setConnectorStatusPendingId] = useState<string | null>(null);
  const [connectorError, setConnectorError] = useState<string | null>(null);

  const [skillSheetOpen, setSkillSheetOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<WorkspaceSkillItem | null>(null);
  const [skillSaving, setSkillSaving] = useState(false);
  const [skillError, setSkillError] = useState<string | null>(null);

  const [scenarioEditorOpen, setScenarioEditorOpen] = useState(false);
  const [editingScenario, setEditingScenario] = useState<ScenarioDefinition | null>(null);
  const [scenarioSaving, setScenarioSaving] = useState(false);

  const [userSaving, setUserSaving] = useState(false);
  const [userError, setUserError] = useState<string | null>(null);

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
        openMetadataConnectors,
        confluenceConnectors,
        jiraConnectors,
        mattermostConnectors,
        pendingInsights,
        scenarios,
        workspaceSkills,
        localUsers,
      }),
    [
      confluenceConnectors,
      mattermostConnectors,
      databaseConnectors,
      jiraConnectors,
      llmConnectors,
      localUsers,
      openMetadataConnectors,
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
      metadata: openMetadataConnectors.filter((item) =>
        matchesConnectorFilter(item, "metadata", deferredSetupSearch, setupStatus)
      ),
      confluence: confluenceConnectors.filter((item) =>
        matchesConnectorFilter(item, "confluence", deferredSetupSearch, setupStatus)
      ),
      jira: jiraConnectors.filter((item) =>
        matchesConnectorFilter(item, "jira", deferredSetupSearch, setupStatus)
      ),
      mattermost: mattermostConnectors.filter((item) =>
        matchesConnectorFilter(item, "mattermost", deferredSetupSearch, setupStatus)
      ),
    }),
    [
      confluenceConnectors,
      databaseConnectors,
      deferredSetupSearch,
      jiraConnectors,
      llmConnectors,
      mattermostConnectors,
      openMetadataConnectors,
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
                database: String(formData.get("database") ?? "").trim() || null,
                catalog: String(formData.get("catalog") ?? "").trim() || null,
                schema: String(formData.get("schema") ?? "").trim() || null,
                secure: formData.get("secure") !== null,
                verify: formData.get("verify") !== null,
                extra_settings: buildDatabaseExtraSettings(formData),
              },
            }),
          }
        );

        startTransition(() => {
          setDatabaseConnectors((current) => upsertById(current, savedConnector as DatabaseConnectorItem));
        });
      } else if (connectorType === "llm") {
        savedConnector = await requestJson<LlmConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              connection: {
                base_url: String(formData.get("base_url") ?? "").trim(),
                model: String(formData.get("model") ?? "").trim(),
                api_key: String(formData.get("api_key") ?? "").trim() || null,
                endpoint_path: String(formData.get("endpoint_path") ?? "").trim() || "/chat/completions",
                verify_tls: formData.get("verify_tls") !== null,
                temperature: Number(String(formData.get("temperature") ?? "0.1")),
                max_tokens: Number(String(formData.get("max_tokens") ?? "1200")),
                headers: {},
                extra_settings: {},
              },
            }),
          }
        );

        startTransition(() => {
          setLlmConnectors((current) => upsertById(current, savedConnector as LlmConnectorItem));
        });
      } else if (connectorType === "metadata") {
        savedConnector = await requestJson<OpenMetadataConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              connection: {
                provider: "openmetadata",
                base_url: String(formData.get("base_url") ?? "").trim(),
                api_token: String(formData.get("api_token") ?? "").trim() || null,
                verify_tls: formData.get("verify_tls") !== null,
                mcp_tables_url: String(formData.get("mcp_tables_url") ?? "").trim() || null,
                service_name: String(formData.get("service_name") ?? "").trim() || null,
                database_name: String(formData.get("database_name") ?? "").trim() || null,
                schema_name: String(formData.get("schema_name") ?? "").trim() || null,
                table_limit: Number(String(formData.get("table_limit") ?? "200")),
                fields: "columns,database,databaseSchema",
                extra_settings: {},
              },
            }),
          }
        );

        startTransition(() => {
          setOpenMetadataConnectors((current) =>
            upsertById(current, savedConnector as OpenMetadataConnectorItem)
          );
        });
      } else if (connectorType === "confluence") {
        savedConnector = await requestJson<ConfluenceConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              connection: {
                provider: "confluence",
                base_url: String(formData.get("base_url") ?? "").trim(),
                username: String(formData.get("username") ?? "").trim() || null,
                api_token: String(formData.get("api_token") ?? "").trim() || null,
                auth_type: String(formData.get("auth_type") ?? "bearer") === "basic" ? "basic" : "bearer",
                verify_tls: formData.get("verify_tls") !== null,
                space_key: String(formData.get("space_key") ?? "").trim() || null,
                page_limit: Number(String(formData.get("page_limit") ?? "10")),
                extra_settings: {},
              },
            }),
          }
        );

        startTransition(() => {
          setConfluenceConnectors((current) =>
            upsertById(current, savedConnector as ConfluenceConnectorItem)
          );
        });
      } else if (connectorType === "jira") {
        savedConnector = await requestJson<JiraConnectorItem>(
          connectorEndpoint(connectorType, connectorId, workspaceId),
          {
            method: isEdit ? "PUT" : "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: String(formData.get("name") ?? "").trim(),
              workspace_id: workspaceId,
              is_active: formData.get("is_active") !== null,
              connection: {
                provider: "jira",
                base_url: String(formData.get("base_url") ?? "").trim(),
                username: String(formData.get("username") ?? "").trim() || null,
                api_token: String(formData.get("api_token") ?? "").trim() || null,
                auth_type: String(formData.get("auth_type") ?? "bearer") === "basic" ? "basic" : "bearer",
                verify_tls: formData.get("verify_tls") !== null,
                project_keys: String(formData.get("project_keys") ?? "")
                  .split(",")
                  .map((item) => item.trim())
                  .filter(Boolean),
                issue_limit: Number(String(formData.get("issue_limit") ?? "10")),
                extra_settings: {},
              },
            }),
          }
        );

        startTransition(() => {
          setJiraConnectors((current) =>
            upsertById(current, savedConnector as JiraConnectorItem)
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
              connection: {
                provider: "mattermost",
                base_url: String(formData.get("base_url") ?? "").trim(),
                bot_token: String(formData.get("bot_token") ?? "").trim() || null,
                bot_username: String(formData.get("bot_username") ?? "").trim() || null,
                verify_tls: formData.get("verify_tls") !== null,
                listen_enabled: formData.get("listen_enabled") !== null,
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
      addToast({
        type: "success",
        title: isEdit ? "Connector updated" : "Connector created",
        description: `${savedConnector.name} is now in the workspace config.`,
      });
    } catch (error) {
      setConnectorError(error instanceof Error ? error.message : "Connector save failed");
    } finally {
      setConnectorSaving(false);
    }
  }

  async function handleSkillSubmit(formData: FormData) {
    setSkillSaving(true);
    setSkillError(null);

    const skillId = String(formData.get("skill_id") ?? "").trim();

    try {
      const saved = await requestJson<WorkspaceSkillItem>(
        skillId ? `/api/admin/skills/${skillId}?workspace_id=${workspaceId}` : "/api/admin/skills",
        {
          method: skillId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workspace_id: workspaceId,
            name: String(formData.get("name") ?? "").trim(),
            capability_family: "sql_optimization_advisor",
            engine_scope: String(formData.get("engine_scope") ?? "clickhouse"),
            priority: Number(String(formData.get("priority") ?? "100")),
            source_kind: "github_markdown_url",
            source_url: String(formData.get("source_url") ?? "").trim(),
            status: String(formData.get("status") ?? "active"),
          }),
        }
      );

      startTransition(() => {
        setWorkspaceSkills((current) => sortSkills(upsertById(current, saved)));
      });
      setSkillSheetOpen(false);
      setEditingSkill(null);
      addToast({
        type: "success",
        title: skillId ? "Skill updated" : "Skill created",
        description: `${saved.name} is ready for runtime validation.`,
      });
    } catch (error) {
      setSkillError(error instanceof Error ? error.message : "Skill save failed");
    } finally {
      setSkillSaving(false);
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
          setLlmConnectors((current) => upsertById(current, savedConnector as LlmConnectorItem));
        } else if (type === "metadata") {
          setOpenMetadataConnectors((current) =>
            upsertById(current, savedConnector as OpenMetadataConnectorItem)
          );
        } else if (type === "confluence") {
          setConfluenceConnectors((current) =>
            upsertById(current, savedConnector as ConfluenceConnectorItem)
          );
        } else if (type === "jira") {
          setJiraConnectors((current) => upsertById(current, savedConnector as JiraConnectorItem));
        } else {
          setMattermostConnectors((current) =>
            upsertById(current, savedConnector as MattermostConnectorItem)
          );
        }
      });
      addToast({
        type: "success",
        title: isActive ? "Connector activated" : "Connector disabled",
        description: `${savedConnector.name} ${isActive ? "is available to runtime." : "is hidden from runtime."}`,
      });
    } catch (error) {
      addToast({
        type: "error",
        title: "Connector status failed",
        description: error instanceof Error ? error.message : "Status update failed",
      });
    } finally {
      setConnectorStatusPendingId(null);
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
        title: "User created",
        description: `${createdUser.login} can sign in now.`,
      });
    } catch (error) {
      startTransition(() => {
        setLocalUsers((current) => current.filter((item) => item.id !== tempId));
      });
      setUserError(error instanceof Error ? error.message : "User create failed");
    } finally {
      setUserSaving(false);
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
        title: action === "approve" ? "Insight verified" : "Insight rejected",
        description: removed.summary,
      });
    } catch (error) {
      startTransition(() => {
        setPendingInsights(existing);
      });
      setReviewError(error instanceof Error ? error.message : "Review action failed");
    } finally {
      setReviewPendingId(null);
    }
  }

  async function handleScenarioSubmit(payload: SaveScenarioPayload, scenarioId?: string) {
    setScenarioSaving(true);
    try {
      const saved = scenarioId
        ? await updateScenario(scenarioId, {
            workspace_id: payload.workspace_id,
            title: payload.title,
            summary: payload.summary,
            description: payload.description,
            prompt_template: payload.prompt_template,
            input_schema: payload.input_schema,
            tool_policy: payload.tool_policy,
            default_connector_bindings: payload.default_connector_bindings,
            status: payload.status,
          })
        : await createScenario(payload);

      startTransition(() => {
        setScenarios((current) => upsertById(current, saved));
      });
      addToast({
        type: "success",
        title: scenarioId ? "Scenario updated" : "Scenario created",
        description: `${saved.title} is in governance now.`,
      });
    } finally {
      setScenarioSaving(false);
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

        if (deleteIntent.connectorType === "metadata") {
          const previous = openMetadataConnectors;
          startTransition(() => {
            setOpenMetadataConnectors((current) =>
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
              setOpenMetadataConnectors(previous);
            });
            throw error;
          }
        }

        if (deleteIntent.connectorType === "confluence") {
          const previous = confluenceConnectors;
          startTransition(() => {
            setConfluenceConnectors((current) =>
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
              setConfluenceConnectors(previous);
            });
            throw error;
          }
        }

        if (deleteIntent.connectorType === "jira") {
          const previous = jiraConnectors;
          startTransition(() => {
            setJiraConnectors((current) =>
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
              setJiraConnectors(previous);
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
          title: "Connector deleted",
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
            title: "Skill deleted",
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
            title: "User deleted",
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
            title: "Scenario archived",
            description: deleteIntent.scenario.title,
          });
        } catch (error) {
          startTransition(() => {
            setScenarios(previous);
          });
          throw error;
        }
      }

      setDeleteIntent(null);
    } catch (error) {
      addToast({
        type: "error",
        title: "Delete failed",
        description: error instanceof Error ? error.message : "Unknown error",
      });
    } finally {
      setDeletePending(false);
    }
  }

  const nextActions = buildNextActions({
    readiness,
    pendingInsights,
    onNavigate: (section) => {
      startSectionTransition(() => setActiveSection(section));
    },
    onAddDatabase: () => openCreateConnectorSheet("database"),
    onAddLlm: () => openCreateConnectorSheet("llm"),
    onReviewQueue: () => startSectionTransition(() => setActiveSection("review")),
    onAddScenario: () => {
      setEditingScenario(null);
      setScenarioEditorOpen(true);
    },
  });

  return (
    <>
      <main className="min-h-screen bg-[radial-gradient(circle_at_top_right,rgba(93,168,255,0.18),transparent_28%),radial-gradient(circle_at_20%_0%,rgba(31,49,81,0.32),transparent_30%),linear-gradient(180deg,#070b14_0%,#060810_100%)] px-4 py-5 text-[#edf3ff] md:px-6 lg:px-8">
        <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-6">
          <section className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(360px,0.9fr)]">
            <Card className="overflow-hidden border-[#22334d] bg-[radial-gradient(circle_at_top_right,rgba(93,168,255,0.18),transparent_30%),linear-gradient(180deg,rgba(10,16,29,0.98),rgba(8,12,22,0.98))]">
              <CardContent className="grid gap-6 p-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:p-7">
                <div className="space-y-5">
                  <div className="flex flex-wrap items-center gap-3">
                    <SectionBadge tone="primary">/admin</SectionBadge>
                    <SectionBadge tone={readiness.blockers.length === 0 ? "success" : "warning"}>
                      {readiness.blockers.length === 0 ? "ready" : `${readiness.blockers.length} blockers`}
                    </SectionBadge>
                    <span className="text-sm text-[#8ea3c5]">Workspace `{workspaceId}`</span>
                  </div>
                  <div className="space-y-3">
                    <h1 className="max-w-3xl text-4xl font-semibold tracking-[-0.05em] text-[#f3f7ff]">
                      Admin that tells you what is broken, what is missing, and what to do next.
                    </h1>
                    <p className="max-w-2xl text-sm leading-7 text-[#8ea3c5]">
                      Connect runtime infrastructure, govern analyst workflows, control access, and clear the review queue without scrolling through junk.
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-3">
                    <Button onClick={() => startSectionTransition(() => setActiveSection("setup"))}>
                      Fix setup
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => startSectionTransition(() => setActiveSection("review"))}
                    >
                      Open review queue
                    </Button>
                    <Link
                      href="/"
                      className="inline-flex h-10 items-center justify-center rounded-lg border border-[#304664] bg-[#0b1322] px-4 text-sm font-medium text-[#d7e6ff] transition hover:bg-[#132033]"
                    >
                      Back to workspace
                    </Link>
                  </div>
                </div>
                <div className="rounded-[28px] border border-[#20324e] bg-[#09111f]/92 p-5 shadow-[0_20px_60px_rgba(0,0,0,0.28)]">
                  <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#6e84a8]">
                    Readiness strip
                  </p>
                  <div className="mt-3 flex items-end justify-between gap-3">
                    <div>
                      <div className="text-4xl font-semibold tracking-[-0.06em] text-[#f1f6ff]">
                        {readiness.readinessScore}%
                      </div>
                      <p className="mt-1 text-sm text-[#8ea3c5]">
                        {readiness.completedChecks}/{readiness.totalChecks} core checks passed
                      </p>
                    </div>
                    <Sparkles className="h-8 w-8 text-[#5da8ff]" />
                  </div>
                  <div className="mt-4 h-2 overflow-hidden rounded-full bg-[#121d31]">
                    <div
                      className="h-full rounded-full bg-[linear-gradient(90deg,#5da8ff,#8fe2ff)]"
                      style={{ width: `${readiness.readinessScore}%` }}
                    />
                  </div>
                  <div className="mt-5 grid gap-3">
                    <div className="flex items-center justify-between rounded-2xl border border-[#1c2a42] bg-[#0b1322] px-3 py-3">
                      <span className="text-sm text-[#b4c6e2]">Secure connections</span>
                      <span className="text-sm font-semibold text-[#eef4ff]">
                        {readiness.totalSecureConnections}/{readiness.totalConnections}
                      </span>
                    </div>
                    <div className="flex items-center justify-between rounded-2xl border border-[#1c2a42] bg-[#0b1322] px-3 py-3">
                      <span className="text-sm text-[#b4c6e2]">Published scenarios</span>
                      <span className="text-sm font-semibold text-[#eef4ff]">
                        {readiness.publishedScenarioCount}
                      </span>
                    </div>
                    <div className="flex items-center justify-between rounded-2xl border border-[#1c2a42] bg-[#0b1322] px-3 py-3">
                      <span className="text-sm text-[#b4c6e2]">Active skills</span>
                      <span className="text-sm font-semibold text-[#eef4ff]">
                        {readiness.activeSkillCount}
                      </span>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>

            <InfoPanel
              title="Next actions"
              description="The page should drive the operator. Here it does."
            >
              <div className="grid gap-3">
                {nextActions.length > 0 ? (
                  nextActions.map((action) => (
                    <button
                      key={action.title}
                      type="button"
                      onClick={action.onClick}
                      className="flex items-center justify-between rounded-[22px] border border-[#22334e] bg-[#0b1322] px-4 py-4 text-left transition hover:border-[#35537a] hover:bg-[#0d1729]"
                    >
                      <div>
                        <p className="text-sm font-medium text-[#eef4ff]">{action.title}</p>
                        <p className="mt-1 text-sm leading-6 text-[#8ea3c5]">{action.description}</p>
                      </div>
                      <ArrowRight className="h-4 w-4 flex-shrink-0 text-[#8fb9ff]" />
                    </button>
                  ))
                ) : (
                  <EmptyState
                    icon={<CheckCircle2 className="h-5 w-5" />}
                    title="No obvious fires"
                    description="Core setup is in place. You can keep tuning governance and review cadence."
                    className="border-[#1f3a2d] bg-[linear-gradient(180deg,rgba(7,18,16,0.92),rgba(6,13,12,0.98))]"
                  />
                )}
              </div>
            </InfoPanel>
          </section>

          <div className="sticky top-4 z-20 rounded-[28px] border border-[#1d2940] bg-[#08101d]/88 p-2 backdrop-blur-xl">
            <div className="grid gap-2 md:grid-cols-5">
              {SECTION_META.map((section) => {
                const isActive = section.id === activeSection;
                return (
                  <button
                    key={section.id}
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => startSectionTransition(() => setActiveSection(section.id))}
                    className={`rounded-[20px] px-4 py-3 text-left transition ${
                      isActive
                        ? "bg-[linear-gradient(180deg,rgba(93,168,255,0.18),rgba(16,30,50,0.98))] text-white shadow-[0_10px_28px_rgba(93,168,255,0.18)]"
                        : "bg-transparent text-[#8ea3c5] hover:bg-[#0d1729] hover:text-white"
                    }`}
                  >
                    <div className="text-sm font-medium">{section.label}</div>
                    <div className="mt-1 text-xs leading-5 opacity-80">{section.description}</div>
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
                  eyebrow="Overview"
                  title="Operate the workspace, not the page"
                  description="Readiness and triage first. Fancy vanity counters can go to hell."
                >
                  <div className="grid gap-4 xl:grid-cols-4">
                    <MetricCard
                      icon={<Sparkles className="h-5 w-5" />}
                      title="Readiness"
                      value={`${readiness.readinessScore}%`}
                      caption="Weighted against core runtime and governance checks"
                      tone="primary"
                    />
                    <MetricCard
                      icon={<Shield className="h-5 w-5" />}
                      title="Secure connections"
                      value={`${readiness.totalSecureConnections}/${readiness.totalConnections}`}
                      caption="TLS-enabled connectors currently configured"
                      tone={readiness.totalSecureConnections === readiness.totalConnections ? "success" : "warning"}
                    />
                    <MetricCard
                      icon={<Workflow className="h-5 w-5" />}
                      title="Published scenarios"
                      value={readiness.publishedScenarioCount}
                      caption="Official workflows that analysts can actually use"
                      tone={readiness.publishedScenarioCount > 0 ? "success" : "warning"}
                    />
                    <MetricCard
                      icon={<Clock3 className="h-5 w-5" />}
                      title="Review queue"
                      value={pendingInsights.length}
                      caption="Pending insight verification items"
                      tone={pendingInsights.length === 0 ? "success" : "warning"}
                    />
                  </div>

                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
                    <InfoPanel
                      title="Critical blockers"
                      description="These items keep readiness score down and usually block analyst value."
                    >
                      {readiness.blockers.length > 0 ? (
                        <div className="grid gap-3">
                          {readiness.blockers.map((blocker) => (
                            <div
                              key={blocker}
                              className="rounded-[22px] border border-[#4b2830] bg-[#1a0f15] px-4 py-4"
                            >
                              <div className="flex items-start gap-3">
                                <XCircle className="mt-0.5 h-5 w-5 flex-shrink-0 text-[#ff7d8b]" />
                                <p className="text-sm leading-6 text-[#ffd7dd]">{blocker}</p>
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <EmptyState
                          icon={<CheckCircle2 className="h-5 w-5" />}
                          title="No blockers"
                          description="Setup is clean enough. Keep the review queue and governance layer disciplined."
                          className="border-[#1f3a2d] bg-[linear-gradient(180deg,rgba(7,18,16,0.92),rgba(6,13,12,0.98))]"
                        />
                      )}
                    </InfoPanel>

                    <InfoPanel
                      title="System pulse"
                      description="Fast glance at the pieces that matter."
                    >
                      <div className="grid gap-3">
                        <SummaryRow label="Database connectors" value={databaseConnectors.length} />
                        <SummaryRow label="LLM connectors" value={llmConnectors.length} />
                        <SummaryRow label="OpenMetadata connectors" value={openMetadataConnectors.length} />
                        <SummaryRow label="Confluence connectors" value={confluenceConnectors.length} />
                        <SummaryRow label="Jira connectors" value={jiraConnectors.length} />
                        <SummaryRow label="Tag connectors" value={mattermostConnectors.length} />
                        <SummaryRow label="Active skills" value={readiness.activeSkillCount} />
                        <SummaryRow label="Local users" value={localUsers.length} />
                      </div>
                    </InfoPanel>
                  </div>
                </SectionShell>
              ) : null}

              {activeSection === "setup" ? (
                <SectionShell
                  eyebrow="Setup"
                  title="Runtime configuration"
                  description="Search, filter, edit, and delete connectors without reloading the whole damned page."
                  actions={
                    <>
                      <Button onClick={() => openCreateConnectorSheet("database")}>
                        <Plus className="h-4 w-4" />
                        Add database
                      </Button>
                      <Button variant="outline" onClick={() => openCreateConnectorSheet("llm")}>
                        <Plus className="h-4 w-4" />
                        Add LLM
                      </Button>
                    </>
                  }
                >
                  <Card className="border-[#1d2940] bg-[#09111f]/92">
                    <CardContent className="grid gap-4 p-5 lg:grid-cols-[minmax(260px,1fr)_auto] lg:items-center">
                      <div className="relative">
                        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#6e84a8]" />
                        <Input
                          value={setupSearch}
                          onChange={(event) => setSetupSearch(event.target.value)}
                          placeholder="Search by connector name, host, model, URL, or service"
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
                                ? "border-[#5da8ff]/40 bg-[#5da8ff]/16 text-[#eef4ff]"
                                : "border-[#263750] bg-[#0b1322] text-[#9bb0cf] hover:border-[#35537a] hover:text-white"
                            }`}
                          >
                            {filter}
                          </button>
                        ))}
                      </div>
                    </CardContent>
                  </Card>

                  <div className="grid gap-4 xl:grid-cols-5">
                    <ConnectorTypeSummaryCard
                      title="Database"
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
                      title="OpenMetadata"
                      count={openMetadataConnectors.length}
                      readyCount={openMetadataConnectors.filter((item) => getConnectorHealth(item, "metadata") === "ready").length}
                      icon={<Layers className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("metadata")}
                    />
                    <ConnectorTypeSummaryCard
                      title="Confluence"
                      count={confluenceConnectors.length}
                      readyCount={confluenceConnectors.filter((item) => getConnectorHealth(item, "confluence") === "ready").length}
                      icon={<BookText className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("confluence")}
                    />
                    <ConnectorTypeSummaryCard
                      title="Jira"
                      count={jiraConnectors.length}
                      readyCount={jiraConnectors.filter((item) => getConnectorHealth(item, "jira") === "ready").length}
                      icon={<Workflow className="h-5 w-5" />}
                      onAdd={() => openCreateConnectorSheet("jira")}
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
                        ["metadata", filteredConnectorCollections.metadata],
                        ["confluence", filteredConnectorCollections.confluence],
                        ["jira", filteredConnectorCollections.jira],
                        ["mattermost", filteredConnectorCollections.mattermost],
                      ] as [ConnectorType, AnyConnectorItem[]][]
                    ).map(([type, items]) => (
                      <InfoPanel
                        key={type}
                        title={`${getConnectorTypeLabel(type)} connectors`}
                        description={`${items.length} items match current filters.`}
                      >
                        {items.length > 0 ? (
                          <LayoutGroup>
                            <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
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
                                  />
                                </motion.div>
                              ))}
                            </div>
                          </LayoutGroup>
                        ) : (
                          <EmptyState
                            icon={type === "database" ? <Database className="h-5 w-5" /> : type === "llm" ? <Server className="h-5 w-5" /> : type === "metadata" ? <Layers className="h-5 w-5" /> : <BookText className="h-5 w-5" />}
                            title={`No ${getConnectorTypeLabel(type).toLowerCase()} connectors for this filter`}
                            description="Either the search is too narrow or the setup is actually empty."
                            action={
                              <Button size="sm" onClick={() => openCreateConnectorSheet(type)}>
                                Add {getConnectorTypeLabel(type)}
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
                  eyebrow="Governance"
                  title="What analysts are allowed to run"
                  description="Official scenarios and workspace skills belong together. One defines workflow, the other defines runtime guidance."
                  actions={
                    <>
                      <Button
                        onClick={() => {
                          setEditingScenario(null);
                          setScenarioEditorOpen(true);
                        }}
                      >
                        <Plus className="h-4 w-4" />
                        Add official scenario
                      </Button>
                      <Button variant="outline" onClick={openCreateSkillSheet}>
                        <Plus className="h-4 w-4" />
                        Add skill
                      </Button>
                    </>
                  }
                >
                  <div className="grid gap-4 xl:grid-cols-2">
                    <InfoPanel
                      title="Official scenarios"
                      description="Published flows visible to analysts and expected to behave."
                    >
                      {officialScenarios.length > 0 ? (
                        <div className="grid gap-4">
                          {officialScenarios.map((scenario) => (
                            <ScenarioCard
                              key={scenario.id}
                              scenario={scenario}
                              onEdit={() => {
                                setEditingScenario(scenario);
                                setScenarioEditorOpen(true);
                              }}
                              onArchive={() => setDeleteIntent({ kind: "scenario", scenario })}
                            />
                          ))}
                        </div>
                      ) : (
                        <EmptyState
                          icon={<Workflow className="h-5 w-5" />}
                          title="No official scenarios"
                          description="If analysts have no trusted workflows, governance is just a slide deck."
                          action={
                            <Button
                              size="sm"
                              onClick={() => {
                                setEditingScenario(null);
                                setScenarioEditorOpen(true);
                              }}
                            >
                              Create scenario
                            </Button>
                          }
                        />
                      )}
                    </InfoPanel>

                    <InfoPanel
                      title="Workspace skills"
                      description="Optimization playbooks and tuning guidance attached to the workspace runtime."
                    >
                      {workspaceSkills.length > 0 ? (
                        <div className="grid gap-4">
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
                          title="No workspace skills"
                          description="Runtime guidance is empty right now. That's lazy governance."
                          action={
                            <Button size="sm" onClick={openCreateSkillSheet}>
                              Add skill
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
                  eyebrow="Access"
                  title="Local accounts and privilege shape"
                  description="User creation stays quick. Destructive actions get a real confirmation flow instead of a caveman browser popup."
                >
                  <div className="grid gap-4 xl:grid-cols-[minmax(340px,0.8fr)_minmax(0,1.2fr)]">
                    <InfoPanel
                      title="Create local user"
                      description="Admin-only access path for local auth workspaces."
                    >
                      <form
                        onSubmit={(event) => {
                          event.preventDefault();
                          void handleCreateLocalUser(new FormData(event.currentTarget), event.currentTarget);
                        }}
                        className="grid gap-4"
                      >
                        <Input data-autofocus label="Login" name="login" placeholder="new-analyst" required />
                        <Input
                          label="Password"
                          type="password"
                          name="password"
                          minLength={8}
                          placeholder="strong-pass-123"
                          required
                        />
                        <label>
                          <span className="mb-2 block text-sm font-medium text-[#c7d8f4]">Role</span>
                          <select
                            name="role"
                            defaultValue="admin"
                            className="flex h-10 w-full rounded-lg border border-[#2e3d59] bg-[#090e1a] px-3 py-2 text-sm text-[#edf3ff] outline-none transition focus:border-transparent focus:ring-2 focus:ring-[#5da8ff]"
                          >
                            <option value="admin">admin</option>
                            <option value="analyst">analyst</option>
                          </select>
                        </label>
                        <Input label="Display name" name="display_name" placeholder="Platform Admin" />

                        {userError ? (
                          <Alert variant="danger" title="Create failed" description={userError} />
                        ) : null}

                        <div className="flex items-center justify-end">
                          <Button type="submit" isLoading={userSaving}>
                            Create user
                          </Button>
                        </div>
                      </form>
                    </InfoPanel>

                    <InfoPanel
                      title="Current users"
                      description="Deletion is optimistic, but rollback-safe if the backend refuses."
                    >
                      {localUsers.length > 0 ? (
                        <div className="grid gap-4 md:grid-cols-2">
                          {localUsers.map((user) => (
                            <Card
                              key={user.id}
                              className="border-[#182438] bg-[linear-gradient(180deg,rgba(10,16,29,0.96),rgba(8,12,22,0.98))]"
                            >
                              <CardContent className="grid gap-4 p-5">
                                <div className="flex items-start justify-between gap-3">
                                  <div className="flex items-center gap-3">
                                    <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-[#22334e] bg-[#0b1322] text-[#8fb9ff]">
                                      <UserRound className="h-5 w-5" />
                                    </div>
                                    <div>
                                      <p className="text-base font-medium text-[#eef4ff]">{user.login}</p>
                                      <p className="text-sm text-[#8ea3c5]">{user.display_name ?? "—"}</p>
                                    </div>
                                  </div>
                                  <div className="flex gap-2">
                                    <SectionBadge tone={user.role === "admin" ? "warning" : "primary"}>
                                      {user.role}
                                    </SectionBadge>
                                    <SectionBadge tone={user.is_active ? "success" : "danger"}>
                                      {user.is_active ? "active" : "disabled"}
                                    </SectionBadge>
                                  </div>
                                </div>
                                <dl className="grid gap-3 text-sm text-[#b4c6e2]">
                                  <SummaryRow label="Provider" value={user.auth_provider} />
                                  <SummaryRow label="Subject" value={user.auth_subject} monospace />
                                  <SummaryRow label="Created" value={formatShortDate(user.created_at)} />
                                </dl>
                                <div className="flex justify-end">
                                  <button
                                    type="button"
                                    title={user.id === currentUserId ? "Current user cannot be deleted" : "Delete"}
                                    disabled={user.id === currentUserId}
                                    onClick={() => {
                                      if (user.id === currentUserId) {
                                        setUserError("Current user cannot be deleted");
                                        return;
                                      }
                                      setDeleteIntent({ kind: "user", user });
                                    }}
                                    className="inline-flex h-10 items-center justify-center rounded-xl border border-[#4a2930] bg-[#1a0f15] px-4 text-sm font-medium text-[#ffb2bb] transition hover:bg-[#24131b] disabled:cursor-not-allowed disabled:opacity-50"
                                  >
                                    <Trash2 className="mr-2 h-4 w-4" />
                                    Delete
                                  </button>
                                </div>
                              </CardContent>
                            </Card>
                          ))}
                        </div>
                      ) : (
                        <EmptyState
                          icon={<Users className="h-5 w-5" />}
                          title="No users"
                          description="That means local auth is barely configured."
                        />
                      )}
                    </InfoPanel>
                  </div>
                </SectionShell>
              ) : null}

              {activeSection === "review" ? (
                <SectionShell
                  eyebrow="Review"
                  title="Pending insight verification queue"
                  description="Queue on the left, detail on the right. High-confidence junk gets rejected fast. Good material gets verified faster."
                >
                  {reviewError ? (
                    <Alert variant="danger" title="Review action failed" description={reviewError} />
                  ) : null}

                  {pendingInsights.length > 0 && selectedReview ? (
                    <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
                      <InfoPanel
                        title="Queue"
                        description={`${pendingInsights.length} pending items`}
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
                                      ? "border-[#4a79b6] bg-[#10203a]"
                                      : "border-[#22334e] bg-[#0b1322] hover:border-[#35537a] hover:bg-[#0d1729]"
                                  }`}
                                >
                                  <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                      <p className="truncate text-sm font-medium text-[#eef4ff]">{item.summary}</p>
                                      <p className="mt-1 line-clamp-2 text-sm leading-6 text-[#8ea3c5]">
                                        {item.answer_excerpt}
                                      </p>
                                    </div>
                                    <div className="flex flex-col items-end gap-2">
                                      <SectionBadge tone={item.llm_confidence >= 0.8 ? "warning" : "primary"}>
                                        {item.llm_confidence.toFixed(2)}
                                      </SectionBadge>
                                      <ReviewTag>{item.likes_count} likes</ReviewTag>
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
                        description={`Created ${formatDateTime(selectedReview.created_at)} • confidence ${selectedReview.llm_confidence.toFixed(2)}`}
                      >
                        <div className="grid gap-5">
                          <div className="grid gap-4 lg:grid-cols-3">
                            <SummaryStat label="Likes" value={selectedReview.likes_count} />
                            <SummaryStat label="Tables" value={selectedReview.context_tables.length} />
                            <SummaryStat label="Sources" value={selectedReview.metadata_sources.length} />
                          </div>

                          <DetailBlock title="Reason" content={selectedReview.llm_reason} />
                          <DetailBlock title="Answer excerpt" content={selectedReview.answer_excerpt} />
                          {selectedReview.generated_sql ? (
                            <DetailCodeBlock title="Generated SQL" content={selectedReview.generated_sql} />
                          ) : null}
                          {selectedReview.article_markdown ? (
                            <DetailCodeBlock title="Article draft" content={selectedReview.article_markdown} />
                          ) : null}

                          <div className="grid gap-4 lg:grid-cols-2">
                            <TagList title="Context tables" items={selectedReview.context_tables} />
                            <TagList title="Metadata sources" items={selectedReview.metadata_sources} />
                            <TagList title="Keywords" items={selectedReview.keywords} />
                            <InfoPanel title="Pointers">
                              <div className="grid gap-3 text-sm text-[#b4c6e2]">
                                <SummaryRow label="Conversation" value={selectedReview.conversation_id} monospace />
                                <SummaryRow label="Message" value={selectedReview.message_id} monospace />
                                <SummaryRow label="Execution" value={selectedReview.execution_id ?? "—"} monospace />
                              </div>
                            </InfoPanel>
                          </div>

                          <div className="flex flex-wrap justify-end gap-3 border-t border-[#182438] pt-4">
                            <Button
                              variant="danger"
                              onClick={() => void handleReview(selectedReview.id, "reject")}
                              disabled={reviewPendingId === selectedReview.id}
                            >
                              <XCircle className="h-4 w-4" />
                              Reject
                            </Button>
                            <Button
                              variant="success"
                              onClick={() => void handleReview(selectedReview.id, "approve")}
                              disabled={reviewPendingId === selectedReview.id}
                            >
                              <CheckCircle2 className="h-4 w-4" />
                              Verify
                            </Button>
                          </div>
                        </div>
                      </InfoPanel>
                    </div>
                  ) : (
                    <EmptyState
                      icon={<CheckCircle2 className="h-5 w-5" />}
                      title="Queue is empty"
                      description="No pending insights. Either quality is under control or the pipeline is asleep."
                    />
                  )}
                </SectionShell>
              ) : null}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>

      <ConnectorSheet
        open={connectorSheetOpen}
        onOpenChange={(open) => {
          setConnectorSheetOpen(open);
          if (!open) {
            setEditingConnector(null);
            setConnectorError(null);
          }
        }}
        connectorType={connectorSheetType}
        editingConnector={editingConnector}
        onSubmit={handleConnectorSubmit}
        saving={connectorSaving}
        error={connectorError}
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
        onSubmit={handleSkillSubmit}
        saving={skillSaving}
        error={skillError}
      />

      <ScenarioEditorModal
        isOpen={scenarioEditorOpen}
        workspaceName={workspaceId}
        scope="official"
        saving={scenarioSaving}
        scenario={editingScenario}
        onClose={() => {
          setScenarioEditorOpen(false);
          setEditingScenario(null);
        }}
        onSubmit={(payload, scenarioId) => {
          void handleScenarioSubmit(payload, scenarioId);
          setScenarioEditorOpen(false);
          setEditingScenario(null);
        }}
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
        confirmLabel={deleteIntent?.kind === "scenario" ? "Archive" : "Delete"}
        onConfirm={() => void confirmDeleteIntent()}
        isPending={deletePending}
      />
    </>
  );
}

function buildDeleteTitle(intent: DeleteIntent | null) {
  if (!intent) {
    return "Delete item";
  }

  if (intent.kind === "connector") {
    return `Delete ${intent.title}?`;
  }
  if (intent.kind === "skill") {
    return `Delete ${intent.title}?`;
  }
  if (intent.kind === "user") {
    return `Delete user ${intent.user.login}?`;
  }
  return `Archive ${intent.scenario.title}?`;
}

function buildDeleteDescription(intent: DeleteIntent | null, currentUserId: string) {
  if (!intent) {
    return "This action cannot be undone.";
  }

  if (intent.kind === "connector") {
    return "The runtime will immediately lose this connector configuration. If another object depends on it, that breakage is on you.";
  }
  if (intent.kind === "skill") {
    return "The workspace will stop using this skill snapshot for runtime optimization.";
  }
  if (intent.kind === "user") {
    return intent.user.id === currentUserId
      ? "Current user cannot be deleted."
      : "The account will lose access immediately.";
  }
  return "The scenario will move to archived state and leave the active governance list.";
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
    <div className="flex items-center justify-between gap-4 rounded-2xl border border-[#182438] bg-[#0b1322] px-3 py-3">
      <span className="text-sm text-[#8ea3c5]">{label}</span>
      <span className={`text-sm font-medium text-[#eef4ff] ${monospace ? "font-mono text-[13px]" : ""}`}>
        {value}
      </span>
    </div>
  );
}

function SummaryStat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-[22px] border border-[#1d2940] bg-[#0b1322] px-4 py-4">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#6e84a8]">{label}</p>
      <div className="mt-2 text-2xl font-semibold tracking-[-0.04em] text-[#eef4ff]">{value}</div>
    </div>
  );
}

function DetailBlock({ title, content }: { title: string; content: string }) {
  return (
    <div className="grid gap-2">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#6e84a8]">{title}</p>
      <div className="rounded-[22px] border border-[#182438] bg-[#0b1322] px-4 py-4 text-sm leading-7 text-[#d5e2f7]">
        {content}
      </div>
    </div>
  );
}

function DetailCodeBlock({ title, content }: { title: string; content: string }) {
  return (
    <div className="grid gap-2">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#6e84a8]">{title}</p>
      <pre className="overflow-x-auto rounded-[22px] border border-[#182438] bg-[#06101f] px-4 py-4 text-xs leading-6 text-[#cfe1ff]">
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
        <p className="text-sm text-[#8ea3c5]">No data</p>
      )}
    </InfoPanel>
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
    <Card className="border-[#1d2940] bg-[linear-gradient(180deg,rgba(10,16,29,0.96),rgba(8,12,22,0.98))]">
      <CardContent className="grid gap-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-[#22334e] bg-[#0b1322] text-[#8fb9ff]">
              {icon}
            </div>
            <div>
              <p className="text-base font-medium text-[#eef4ff]">{title}</p>
              <p className="text-sm text-[#8ea3c5]">{readyCount}/{count} ready</p>
            </div>
          </div>
          <SectionBadge tone={count > 0 && readyCount === count ? "success" : count > 0 ? "warning" : "danger"}>
            {count}
          </SectionBadge>
        </div>
        <Button size="sm" variant="outline" onClick={onAdd}>
          <Plus className="h-4 w-4" />
          Add {title}
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
}: {
  connector: AnyConnectorItem;
  type: ConnectorType;
  statusPending: boolean;
  onToggleActive: (isActive: boolean) => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const health = getConnectorHealth(connector, type);
  const healthTone = connector.is_active ? (health === "ready" ? "success" : "warning") : "danger";

  return (
    <Card className="border-[#182438] bg-[linear-gradient(180deg,rgba(10,16,29,0.96),rgba(8,12,22,0.98))]">
      <CardContent className="grid gap-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-base font-medium text-[#eef4ff]">{connector.name}</p>
            <p className="mt-1 text-sm text-[#8ea3c5]">{getConnectorSubtitle(connector, type)}</p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <SectionBadge tone={healthTone}>{connector.is_active ? health : "inactive"}</SectionBadge>
            <Switch
              aria-label={`${connector.name} active`}
              checked={connector.is_active}
              disabled={statusPending}
              onChange={(event) => onToggleActive(event.currentTarget.checked)}
              label={connector.is_active ? "Active" : "Inactive"}
            />
          </div>
        </div>
        <div className="grid gap-3 text-sm text-[#b4c6e2]">
          {getConnectorDetailRows(connector, type).map((row) => (
            <SummaryRow key={row.label} label={row.label} value={row.value} monospace={row.monospace} />
          ))}
        </div>
        <div className="flex flex-wrap justify-between gap-3 border-t border-[#182438] pt-4">
          <span className="text-xs uppercase tracking-[0.16em] text-[#6e84a8]">
            Updated {formatShortDate(connector.updated_at)}
          </span>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={onEdit} className="border border-[#24344f] bg-[#0b1322] text-[#d7e6ff] hover:bg-[#132033]">
              <Edit className="h-4 w-4" />
              Edit
            </Button>
            <Button size="sm" variant="danger" onClick={onDelete}>
              <Trash2 className="h-4 w-4" />
              Delete
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
    <Card className="border-[#182438] bg-[linear-gradient(180deg,rgba(10,16,29,0.96),rgba(8,12,22,0.98))]">
      <CardContent className="grid gap-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-base font-medium text-[#eef4ff]">{scenario.title}</p>
            <p className="mt-1 text-sm text-[#8ea3c5]">{scenario.summary}</p>
          </div>
          <SectionBadge tone={scenario.status === "published" ? "success" : "warning"}>
            {scenario.status}
          </SectionBadge>
        </div>
        <div className="grid gap-3 text-sm text-[#b4c6e2]">
          <SummaryRow label="Slug" value={scenario.slug} monospace />
          <SummaryRow label="Version" value={`v${scenario.version}`} />
          <SummaryRow label="Updated" value={formatShortDate(scenario.updated_at)} />
        </div>
        <div className="flex justify-end gap-2 border-t border-[#182438] pt-4">
          <Button size="sm" variant="ghost" onClick={onEdit} className="border border-[#24344f] bg-[#0b1322] text-[#d7e6ff] hover:bg-[#132033]">
            <Edit className="h-4 w-4" />
            Edit
          </Button>
          <Button size="sm" variant="danger" onClick={onArchive}>
            <Trash2 className="h-4 w-4" />
            Archive
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
    <Card className="border-[#182438] bg-[linear-gradient(180deg,rgba(10,16,29,0.96),rgba(8,12,22,0.98))]">
      <CardContent className="grid gap-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-base font-medium text-[#eef4ff]">{skill.name}</p>
            <p className="mt-1 text-sm text-[#8ea3c5]">{skill.summary ?? "No summary fetched yet."}</p>
          </div>
          <div className="flex flex-col gap-2">
            <SectionBadge tone={skill.status === "active" ? "success" : "warning"}>{skill.status}</SectionBadge>
            <SectionBadge tone={skill.validation_status === "valid" ? "success" : skill.validation_status === "invalid" ? "danger" : "warning"}>
              {skill.validation_status}
            </SectionBadge>
          </div>
        </div>
        <div className="grid gap-3 text-sm text-[#b4c6e2]">
          <SummaryRow label="Priority" value={skill.priority} />
          <SummaryRow label="Scope" value={skill.engine_scope} />
          <SummaryRow label="Source" value={skill.source_url} monospace />
          <SummaryRow label="Validated" value={formatDateTime(skill.last_validated_at)} />
        </div>
        <div className="flex justify-end gap-2 border-t border-[#182438] pt-4">
          <Button size="sm" variant="ghost" onClick={onEdit} className="border border-[#24344f] bg-[#0b1322] text-[#d7e6ff] hover:bg-[#132033]">
            <Edit className="h-4 w-4" />
            Edit
          </Button>
          <Button size="sm" variant="danger" onClick={onDelete}>
            <Trash2 className="h-4 w-4" />
            Delete
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
  if (type === "metadata") {
    const item = connector as OpenMetadataConnectorItem;
    return `${item.connection.service_name ?? "No default service"} • ${item.connection.base_url}`;
  }
  if (type === "confluence") {
    const item = connector as ConfluenceConnectorItem;
    return `${item.connection.auth_type} auth • ${item.connection.base_url}`;
  }
  if (type === "jira") {
    const item = connector as JiraConnectorItem;
    return `${item.connection.auth_type} auth • ${item.connection.base_url}`;
  }
  const item = connector as MattermostConnectorItem;
  return `${item.connection.bot_username ?? "username auto"} • ${item.connection.base_url}`;
}

function getConnectorDetailRows(
  connector: AnyConnectorItem,
  type: ConnectorType
): Array<{ label: string; value: React.ReactNode; monospace?: boolean }> {
  if (type === "database") {
    const item = connector as DatabaseConnectorItem;
    return [
      { label: "Database", value: item.connection.database ?? item.connection.catalog ?? "—" },
      { label: "Schema", value: item.connection.schema ?? "—" },
      { label: "Security", value: item.connection.secure ? "TLS enabled" : "No TLS" },
      { label: "Password", value: item.has_password ? "stored" : "missing" },
    ];
  }
  if (type === "llm") {
    const item = connector as LlmConnectorItem;
    return [
      { label: "Endpoint", value: item.connection.endpoint_path, monospace: true },
      { label: "TLS", value: item.connection.verify_tls ? "verified" : "disabled" },
      { label: "API key", value: item.has_api_key ? "stored" : "missing" },
      { label: "Max tokens", value: item.connection.max_tokens ?? "—" },
    ];
  }
  if (type === "metadata") {
    const item = connector as OpenMetadataConnectorItem;
    return [
      { label: "Service", value: item.connection.service_name ?? "—" },
      { label: "Database", value: item.connection.database_name ?? "—" },
      { label: "Schema", value: item.connection.schema_name ?? "—" },
      { label: "Token", value: item.has_api_token ? "stored" : "missing" },
    ];
  }
  if (type === "confluence") {
    const item = connector as ConfluenceConnectorItem;
    return [
      { label: "Space", value: item.connection.space_key ?? "—" },
      { label: "Username", value: item.connection.username ?? "—" },
      { label: "Page limit", value: item.connection.page_limit },
      { label: "Token", value: item.has_api_token ? "stored" : "missing" },
    ];
  }
  if (type === "jira") {
    const item = connector as JiraConnectorItem;
    return [
      { label: "Projects", value: item.connection.project_keys.join(", ") || "—" },
      { label: "Username", value: item.connection.username ?? "—" },
      { label: "Issue limit", value: item.connection.issue_limit },
      { label: "Token", value: item.has_api_token ? "stored" : "missing" },
    ];
  }
  const item = connector as MattermostConnectorItem;
  return [
    { label: "Bot", value: item.connection.bot_username ?? "auto" },
    { label: "Listening", value: item.connection.listen_enabled ? "enabled" : "disabled" },
    { label: "TLS", value: item.connection.verify_tls ? "verified" : "disabled" },
    { label: "Token", value: item.has_bot_token ? "stored" : "missing" },
  ];
}

function buildNextActions(args: {
  readiness: ReturnType<typeof buildReadinessModel>;
  pendingInsights: RagInsightReviewItem[];
  onNavigate: (section: AdminSectionId) => void;
  onAddDatabase: () => void;
  onAddLlm: () => void;
  onReviewQueue: () => void;
  onAddScenario: () => void;
}) {
  const actions: Array<{ title: string; description: string; onClick: () => void }> = [];

  if (args.readiness.blockers.some((item) => item.includes("database connector"))) {
    actions.push({
      title: "Add a database connector",
      description: "Without it, the workspace cannot answer anything useful.",
      onClick: args.onAddDatabase,
    });
  }

  if (args.readiness.blockers.some((item) => item.includes("LLM connector"))) {
    actions.push({
      title: "Add an LLM connector",
      description: "Chat runtime is dead until a model endpoint exists.",
      onClick: args.onAddLlm,
    });
  }

  if (args.pendingInsights.length > 0) {
    actions.push({
      title: "Clear the review queue",
      description: `${args.pendingInsights.length} insights need a human verdict.`,
      onClick: args.onReviewQueue,
    });
  }

  if (args.readiness.publishedScenarioCount === 0) {
    actions.push({
      title: "Publish at least one official scenario",
      description: "Governance with no approved workflow is cosplay.",
      onClick: args.onAddScenario,
    });
  }

  if (actions.length === 0) {
    actions.push({
      title: "Review setup details",
      description: "Everything critical is present. Now keep the edges clean.",
      onClick: () => args.onNavigate("setup"),
    });
  }

  return actions.slice(0, 4);
}

function upsertById<T extends { id: string }>(items: T[], nextItem: T) {
  const next = items.filter((item) => item.id !== nextItem.id);
  return [nextItem, ...next];
}
