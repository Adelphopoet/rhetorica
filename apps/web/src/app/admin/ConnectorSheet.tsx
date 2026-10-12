"use client";

import { additionalLlmSettings } from "./llmSettings";
import * as React from "react";
import { useSessionState } from "./useSheetSession";

import { Alert, Badge, Button, Input, Sheet, Switch, Textarea } from "@rhetorica/ui";

import type {
  AnyConnectorItem,
  AirflowConnectorItem,
  ConnectorType,
  DatabaseExtraSetting,
  DatabaseConnectorItem,
  DocumentationConnectorItem,
  GitLabConnectorItem,
  KubernetesConnectorItem,
  LlmConnectorItem,
  MattermostConnectorItem,
  McpConnectorItem,
  VictoriaMetricsConnectorItem,
} from "./types";
import { getConnectorTypeLabel } from "./adminUtils";
import type { AgentProfile } from "@/features/agents/agentsApi";

type ConnectorSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connectorType: ConnectorType | null;
  editingConnector: AnyConnectorItem | null;
  onSubmit: (formData: FormData) => Promise<void>;
  saving: boolean;
  error: string | null;
  agents?: AgentProfile[];
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
      <span className="mb-1 block text-sm font-medium text-primary">{label}</span>
      {children}
    </label>
  );
}

function NativeSelect(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className="flex h-10 w-full rounded-lg border border-control-border bg-surface px-3 py-2 text-sm text-primary outline-none transition focus:border-transparent focus:ring-2 focus:ring-accent"
    />
  );
}

const CONNECTOR_TYPE_PHRASES: Record<ConnectorType, string> = {
  database: "к базе данных",
  llm: "к LLM",
  documentation: "к документации",
  gitlab: "GitLab",
  mcp: "MCP",
  airflow: "Airflow",
  kubernetes: "Kubernetes",
  victoriametrics: "VictoriaMetrics",
  mattermost: "Tag",
};

export function ConnectorSheet({
  open,
  onOpenChange,
  connectorType,
  editingConnector,
  onSubmit,
  saving,
  error,
  agents = [],
  sessionNonce = 0,
}: ConnectorSheetProps) {
  const isEdit = editingConnector != null;

  // Remount the form only when the opened session changes (type, edited connector,
  // or a completed save). Closing and reopening without a save keeps the form
  // mounted so in-progress values survive accidental closes.
  const sessionKey = `${connectorType ?? "none"}:${editingConnector?.id ?? "new"}:${sessionNonce}`;
  const [activeType, setActiveType] = useSessionState<ConnectorType>(
    sessionKey,
    () => connectorType ?? "database"
  );

  const title = `${isEdit ? "Редактирование" : "Добавление"} подключения ${CONNECTOR_TYPE_PHRASES[activeType]}`;

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description="Настройки сохраняются, пока список обновляется оптимистично. Секреты остаются прежними, если их не заменить"
      keepMounted
    >
      <div className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {(["database", "llm", "documentation", "gitlab", "mcp", "airflow", "kubernetes", "victoriametrics", "mattermost"] as ConnectorType[]).map((type) => (
            <button
              key={type}
              type="button"
              disabled={isEdit}
              onClick={() => setActiveType(type)}
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm transition ${
                activeType === type
                  ? "border-accent/40 bg-accent/[0.16] text-primary"
                  : "border-border-strong bg-surface text-secondary hover:border-border-strong hover:text-primary"
              } ${isEdit ? "cursor-not-allowed opacity-70" : ""}`}
            >
              {getConnectorTypeLabel(type)}
            </button>
          ))}
        </div>

        {error ? (
          <Alert
            variant="danger"
            title="Ошибка сохранения"
            description={error}
          />
        ) : null}

        <form
          key={sessionKey}
          onSubmit={(event) => {
            event.preventDefault();
            void onSubmit(new FormData(event.currentTarget));
          }}
          className="space-y-3"
        >
          <input type="hidden" name="connector_id" value={editingConnector?.id ?? ""} />
          <input type="hidden" name="connector_type" value={activeType} />
          <div className="rounded-lg border border-border-strong bg-surface px-4 py-3">
            <Switch
              name="is_active"
              defaultChecked={editingConnector?.is_active ?? true}
              label="Активно"
            />
          </div>

          {activeType === "database" ? (
            <DatabaseConnectorFields
              connector={editingConnector as DatabaseConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          {activeType === "llm" ? (
            <LlmConnectorFields
              connector={editingConnector as LlmConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          {activeType === "documentation" ? (
            <DocumentationConnectorFields
              connector={editingConnector as DocumentationConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          {activeType === "gitlab" ? (
            <GitLabConnectorFields
              connector={editingConnector as GitLabConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          {activeType === "mcp" ? (
            <McpConnectorFields
              connector={editingConnector as McpConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          {activeType === "airflow" ? (
            <AirflowConnectorFields
              connector={editingConnector as AirflowConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          {activeType === "kubernetes" ? (
            <KubernetesConnectorFields
              connector={editingConnector as KubernetesConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          {activeType === "victoriametrics" ? (
            <VictoriaMetricsConnectorFields
              connector={editingConnector as VictoriaMetricsConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          {activeType === "mattermost" ? (
            <MattermostConnectorFields
              connector={editingConnector as MattermostConnectorItem | null}
              isEdit={isEdit}
              agents={agents}
            />
          ) : null}

          <div className="rounded-lg border border-border bg-surface/90 p-4">
            <div className="flex items-center gap-2">
              <Badge variant="info">{isEdit ? "Обновление" : "Создание"}</Badge>
              <p className="text-sm text-primary">
                Конфигурация должна быть плотной. Администраторам нужна скорость, а не церемонии.
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-3">
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              className="border border-border-strong bg-surface text-primary hover:bg-elevated"
            >
              Отмена
            </Button>
            <Button type="submit" isLoading={saving}>
              {isEdit ? "Сохранить подключение" : "Создать подключение"}
            </Button>
          </div>
        </form>
      </div>
    </Sheet>
  );
}

type TrinoCredentialRow = {
  id: string;
  name: string;
  stored: boolean;
};

function DatabaseConnectorFields({
  connector,
  isEdit,
}: {
  connector: DatabaseConnectorItem | null;
  isEdit: boolean;
}) {
  const initialEngine = connector?.connection.engine ?? "clickhouse";
  const [engine, setEngine] = React.useState<DatabaseConnectorItem["connection"]["engine"]>(
    initialEngine
  );
  const queryTimeoutSeconds =
    connector?.connection.extra_settings?.query_timeout_seconds ??
    connector?.connection.extra_settings?.query_timeout_sec ??
    connector?.connection.extra_settings?.sql_timeout_seconds ??
    "";
  const [credentialRows, setCredentialRows] = React.useState<TrinoCredentialRow[]>(() =>
    Object.keys(connector?.connection.extra_credentials ?? {})
      .sort()
      .map((name) => ({ id: name, name, stored: true }))
  );

  React.useEffect(() => {
    setEngine(connector?.connection.engine ?? "clickhouse");
    setCredentialRows(
      Object.keys(connector?.connection.extra_credentials ?? {})
        .sort()
        .map((name) => ({ id: name, name, stored: true }))
    );
  }, [connector]);

  const additionalSettings = databaseAdditionalSettings(
    connector?.connection.extra_settings ?? {},
    initialEngine
  );

  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Название">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="clickhouse-prod"
          required
        />
      </Field>
      <Field label="Движок">
        <NativeSelect
          name="engine"
          value={engine}
          onChange={(event) =>
            setEngine(event.target.value as DatabaseConnectorItem["connection"]["engine"])
          }
        >
          <option value="clickhouse">ClickHouse</option>
          <option value="greenplum">Greenplum</option>
          <option value="impala">Impala</option>
          <option value="trino">Trino</option>
        </NativeSelect>
      </Field>
      <Field label="Хост">
        <Input name="host" defaultValue={connector?.connection.host ?? ""} placeholder="host.example.com" required />
      </Field>
      <Field label="Порт">
        <Input
          type="number"
          name="port"
          defaultValue={connector?.connection.port ?? 8123}
          min={1}
          max={65535}
          required
        />
      </Field>
      <Field label="Имя пользователя">
        <Input name="username" defaultValue={connector?.connection.username ?? ""} placeholder="analyst" required />
      </Field>
      <Field label={isEdit ? "Пароль (оставьте пустым, чтобы не менять)" : "Пароль"}>
        <Input type="password" name="password" placeholder="••••••••" />
      </Field>
      <Field label="Когда использовать" fullWidth>
        <Textarea
          name="usage_description"
          defaultValue={connector?.connection.usage_description ?? ""}
          maxLength={1000}
          placeholder="Для управляемой аналитики витрин и операционных процессов по производственным данным DWH"
        />
      </Field>
      <Field label="База данных">
        <Input name="database" defaultValue={connector?.connection.database ?? ""} placeholder="analytics" />
      </Field>
      <Field label="Каталог">
        <Input name="catalog" defaultValue={connector?.connection.catalog ?? ""} placeholder="hive" />
      </Field>
      <Field label="Схема">
        <Input name="schema" defaultValue={connector?.connection.schema ?? ""} placeholder="dm" />
      </Field>
      {engine === "impala" ? (
        <>
          <Field label="Механизм аутентификации">
            <NativeSelect
              name="auth_type"
              defaultValue={String(connector?.connection.extra_settings?.auth_type ?? "LDAP")}
            >
              <option value="LDAP">LDAP</option>
              <option value="PLAIN">Plain</option>
              <option value="NOSASL">No SASL</option>
              <option value="GSSAPI">Kerberos</option>
              <option value="JWT">JWT</option>
            </NativeSelect>
          </Field>
          <Field label="Повторные попытки">
            <Input
              type="number"
              name="retries"
              defaultValue={String(connector?.connection.extra_settings?.retries ?? 1)}
              min={0}
              max={20}
            />
          </Field>
          <Field label="Путь к сертификату CA">
            <Input
              name="ca_cert"
              defaultValue={String(connector?.connection.extra_settings?.ca_cert ?? "")}
              placeholder="/etc/ssl/certs/impala.pem"
            />
          </Field>
          <Field label="HTTP-путь">
            <Input
              name="http_path"
              defaultValue={String(connector?.connection.extra_settings?.http_path ?? "")}
              placeholder="cliservice"
            />
          </Field>
        </>
      ) : null}
      <Field label="Таймаут SQL, сек">
        <Input
          type="number"
          name="query_timeout_seconds"
          defaultValue={String(queryTimeoutSeconds)}
          min={1}
          max={900}
        />
      </Field>
      {engine === "impala" ? (
        <Field label="Таймаут подключения, сек">
          <Input
            type="number"
            name="timeout"
            defaultValue={String(connector?.connection.extra_settings?.timeout ?? "")}
            min={1}
            max={300}
          />
        </Field>
      ) : null}
      {engine === "trino" ? (
        <div className="space-y-3 md:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-primary">Дополнительные учётные данные Trino</p>
              <p className="text-xs text-secondary">
                Передаются через сквозную авторизацию. Сохранённые значения остаются секретными.
              </p>
            </div>
            <Button
              type="button"
              variant="ghost"
              onClick={() =>
                setCredentialRows((current) => [
                  ...current,
                  {
                    id: `new-${current.length}-${Date.now()}`,
                    name: "",
                    stored: false,
                  },
                ])
              }
              className="border border-border-strong bg-surface text-primary hover:bg-elevated"
            >
              Добавить учётные данные
            </Button>
          </div>
          {credentialRows.length === 0 ? (
            <p className="rounded-xl border border-dashed border-border-strong px-3 py-3 text-sm text-secondary">
              Дополнительные учётные данные не настроены.
            </p>
          ) : null}
          {credentialRows.map((row) => (
            <div
              key={row.id}
              className="grid gap-2 rounded-xl border border-border-strong p-3 md:grid-cols-[1fr_1fr_auto]"
            >
              <Input
                name="extra_credential_name"
                value={row.name}
                readOnly={row.stored}
                onChange={(event) =>
                  setCredentialRows((current) =>
                    current.map((item) =>
                      item.id === row.id ? { ...item, name: event.target.value } : item
                    )
                  )
                }
                placeholder="td_user"
                required
              />
              <Input
                type="password"
                name="extra_credential_value"
                placeholder={row.stored ? "Оставьте пустым, чтобы не менять" : "Значение учётных данных"}
                required={!row.stored}
              />
              <input
                type="hidden"
                name="extra_credential_stored"
                value={row.stored ? "true" : "false"}
              />
              <Button
                type="button"
                variant="ghost"
                onClick={() =>
                  setCredentialRows((current) => current.filter((item) => item.id !== row.id))
                }
                className="border border-danger-border bg-purple-subtle text-danger hover:bg-purple-subtle"
              >
                Удалить
              </Button>
            </div>
          ))}
        </div>
      ) : null}
      <Field label="Дополнительные настройки (JSON)" fullWidth>
        <Textarea
          name="additional_settings_json"
          defaultValue={JSON.stringify(additionalSettings, null, 2)}
          rows={8}
          spellCheck={false}
          placeholder={'{\n  "set_query_max_run_time": false,\n  "http_headers": {"Host": "trino.example.com"}\n}'}
        />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input name="secure" type="checkbox" defaultChecked={connector?.connection.secure} />
        Использовать TLS
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input name="verify" type="checkbox" defaultChecked={connector?.connection.verify ?? true} />
        Проверять сертификат TLS
      </label>
      {engine === "impala" ? (
        <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
          <input
            name="use_http_transport"
            type="checkbox"
            defaultChecked={Boolean(connector?.connection.extra_settings?.use_http_transport)}
          />
          Использовать транспорт HTTP
        </label>
      ) : null}
    </div>
  );
}

function databaseAdditionalSettings(
  settings: Record<string, DatabaseExtraSetting>,
  engine: DatabaseConnectorItem["connection"]["engine"]
): Record<string, DatabaseExtraSetting> {
  const managed = new Set([
    "query_timeout_seconds",
    "query_timeout_sec",
    "sql_timeout_seconds",
  ]);
  if (engine === "impala") {
    ["auth_type", "retries", "ca_cert", "http_path", "timeout", "use_http_transport"].forEach(
      (key) => managed.add(key)
    );
  }
  return Object.fromEntries(Object.entries(settings).filter(([key]) => !managed.has(key)));
}

function LlmConnectorFields({
  connector,
  isEdit,
}: {
  connector: LlmConnectorItem | null;
  isEdit: boolean;
}) {
  const storedKeyCount = connector?.api_key_count ?? (connector?.has_api_key ? 1 : 0);
  const contextWindow =
    connector?.connection.extra_settings?.context_window_tokens ??
    connector?.connection.extra_settings?.context_window ??
    "";
  const [nativeCompactionEnabled, setNativeCompactionEnabled] = React.useState(
    connector?.connection.extra_settings?.native_compaction_enabled === true
  );
  const [retainedKeyIndexes, setRetainedKeyIndexes] = React.useState<number[]>(() =>
    Array.from({ length: storedKeyCount }, (_, index) => index)
  );
  const [apiKeyFields, setApiKeyFields] = React.useState(1);

  React.useEffect(() => {
    setRetainedKeyIndexes(Array.from({ length: storedKeyCount }, (_, index) => index));
    setApiKeyFields(1);
  }, [storedKeyCount, connector?.id]);

  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Название">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="openrouter-main"
          required
        />
      </Field>
      <Field label="Базовый URL">
        <Input
          name="base_url"
          defaultValue={connector?.connection.base_url ?? ""}
          placeholder="https://openrouter.ai/api/v1"
          required
        />
      </Field>
      <Field label="Модель">
        <Input
          name="model"
          defaultValue={connector?.connection.model ?? ""}
          placeholder="openai/gpt-4.1"
          required
        />
      </Field>
      <div className="md:col-span-2">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
          <div>
            <span className="block text-sm font-medium text-primary">
              {isEdit ? "Ключи API (оставьте пустыми, чтобы сохранить имеющиеся)" : "Ключи API"}
            </span>
            {isEdit ? (
              <span className="mt-1 block text-xs text-secondary">
                Сохранено: {storedKeyCount}. Удаляйте сохранённые ключи по одному или добавляйте новые.
              </span>
            ) : null}
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={() => setApiKeyFields((count) => Math.min(count + 1, 50))}
            className="border border-border-strong bg-surface text-primary hover:bg-elevated"
          >
            Добавить ключ
          </Button>
        </div>
        <div className="grid min-w-0 gap-3">
          {isEdit && storedKeyCount > 0 ? (
            <div className="grid gap-2">
              {Array.from({ length: storedKeyCount }, (_, index) => {
                const retained = retainedKeyIndexes.includes(index);
                return (
                  <div
                    key={index}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border-strong bg-surface px-3 py-2 text-sm text-primary"
                  >
                    {retained ? (
                      <input type="hidden" name="retained_api_key_indexes" value={index} />
                    ) : null}
                    <span className={retained ? "text-primary" : "text-muted line-through"}>
                      Сохранённый ключ #{index + 1}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() =>
                        setRetainedKeyIndexes((current) =>
                          retained
                            ? current.filter((item) => item !== index)
                            : [...current, index].sort((left, right) => left - right)
                        )
                      }
                      className="border border-border-strong bg-surface text-primary hover:bg-elevated"
                    >
                      {retained ? "Удалить" : "Оставить"}
                    </Button>
                  </div>
                );
              })}
            </div>
          ) : null}
          {Array.from({ length: apiKeyFields }, (_, index) => (
            <div key={index} className="flex gap-2">
              <Input
                type="password"
                name="api_keys"
                placeholder={`sk-... #${index + 1}`}
              />
              {apiKeyFields > 1 ? (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setApiKeyFields((count) => Math.max(1, count - 1))}
                  className="border border-border-strong bg-surface text-primary hover:bg-elevated"
                >
                  Удалить
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      </div>
      <Field label="Путь эндпоинта">
        <Input
          name="endpoint_path"
          defaultValue={connector?.connection.endpoint_path ?? "/chat/completions"}
          required
        />
      </Field>
      <Field label="Температура">
        <Input
          type="number"
          name="temperature"
          step="any"
          min={0}
          max={2}
          placeholder="По умолчанию провайдера"
          defaultValue={connector ? (connector.connection.temperature ?? "") : 0.1}
        />
      </Field>
      <Field label="Top P">
        <Input
          type="number"
          name="top_p"
          aria-label="Top P"
          step="any"
          min={0}
          max={1}
          placeholder="По умолчанию провайдера"
          defaultValue={connector?.connection.top_p ?? ""}
        />
        <span className="mt-1 block text-xs text-secondary">0–1. Оставьте пустым, чтобы использовать значение провайдера.</span>
      </Field>
      <Field label="Уровень reasoning">
        <NativeSelect
          name="reasoning_effort"
          aria-label="Уровень reasoning"
          defaultValue={connector?.connection.reasoning_effort ?? ""}
        >
          <option value="">По умолчанию провайдера</option>
          <option value="none">none</option>
          <option value="minimal">minimal</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
          <option value="xhigh">xhigh</option>
          <option value="max">max</option>
        </NativeSelect>
        <span className="mt-1 block text-xs text-secondary">Доступные уровни зависят от модели и провайдера</span>
      </Field>
      <Field label="Максимум токенов ответа">
        <Input
          type="number"
          name="max_tokens"
          min={1}
          max={32000}
          placeholder="По умолчанию провайдера"
          defaultValue={connector ? (connector.connection.max_tokens ?? "") : 1200}
        />
      </Field>
      <Field label="Параметр запроса максимума токенов">
        <NativeSelect
          name="max_tokens_parameter"
          defaultValue={connector?.connection.max_tokens_parameter ?? "max_tokens"}
        >
          <option value="max_tokens">max_tokens</option>
          <option value="max_completion_tokens">max_completion_tokens</option>
        </NativeSelect>
      </Field>
      <Field label="Токены контекстного окна">
        <Input
          type="number"
          name="context_window_tokens"
          min={1}
          max={1000000}
          defaultValue={typeof contextWindow === "number" || typeof contextWindow === "string" ? contextWindow : ""}
        />
      </Field>
      <Field label="Хранение ответов у провайдера (store)" fullWidth>
        <NativeSelect
          name="store"
          aria-label="Хранение ответов у провайдера (store)"
          defaultValue={typeof connector?.connection.extra_settings?.store === "boolean"
            ? String(connector.connection.extra_settings.store)
            : ""}
        >
          <option value="">По умолчанию провайдера</option>
          <option value="false">Не сохранять (false)</option>
          <option value="true">Сохранять (true)</option>
        </NativeSelect>
        <span className="mt-1 block text-xs text-secondary">
          Передаёт параметр store в API, если провайдер его поддерживает. История диалогов Rhetorica сохраняется независимо от этого выбора
        </span>
      </Field>
      <label className="md:col-span-2 flex items-start gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          aria-label="Нативное сжатие контекста"
          name="native_compaction_enabled"
          type="checkbox"
          checked={nativeCompactionEnabled}
          onChange={(event) => setNativeCompactionEnabled(event.target.checked)}
          className="mt-1"
        />
        <span>
          Нативное сжатие контекста
          <span className="mt-1 block text-xs text-secondary">
            Использовать сжатие на стороне провайдера через Responses API. Включайте только при поддержке провайдером
          </span>
        </span>
      </label>
      <Field label="Порог нативного сжатия (токены)" fullWidth>
        <Input
          type="number"
          name="native_compaction_threshold_tokens"
          min={1}
          max={1000000}
          step={1}
          required={nativeCompactionEnabled}
          disabled={!nativeCompactionEnabled}
          defaultValue={Number.isInteger(connector?.connection.extra_settings?.native_compaction_threshold_tokens)
            ? Number(connector?.connection.extra_settings?.native_compaction_threshold_tokens)
            : ""}
        />
      </Field>
      <Field label="JSON HTTP-заголовков" fullWidth>
        <Textarea aria-label="JSON HTTP-заголовков" name="llm_headers_json" rows={3}
          defaultValue={JSON.stringify(connector?.connection.headers ?? {}, null, 2)} />
        <span className="mt-1 block text-xs text-secondary">Публичные заголовки, отправляемые провайдеру. Учётные данные храните в ключах API.</span>
      </Field>
      <Field label="JSON дополнительных настроек LLM" fullWidth>
        <Textarea aria-label="JSON дополнительных настроек LLM" name="llm_extra_settings_json" rows={6}
          defaultValue={JSON.stringify(additionalLlmSettings(connector?.connection.extra_settings), null, 2)} />
        <span className="mt-1 block text-xs text-secondary">Прочие параметры подключения, включая reasoning, структурированный вывод и повторные попытки. Значения должны быть строками, числами или логическими. На запросы влияют только поддерживаемые параметры.</span>
      </Field>
      {connector ? (
        <details className="md:col-span-2 text-sm text-secondary">
          <summary>Сведения о подключении</summary>
          <dl className="mt-2 grid gap-1 break-all">
            <dt>ID</dt><dd>{connector.id}</dd>
            <dt>Рабочее пространство</dt><dd>{connector.workspace_id}</dd>
            <dt>Создано</dt><dd>{connector.created_at}</dd>
            <dt>Обновлено</dt><dd>{connector.updated_at}</dd>
          </dl>
          {connector.capability_profile ? <pre className="mt-2 whitespace-pre-wrap">{JSON.stringify(connector.capability_profile, null, 2)}</pre> : null}
        </details>
      ) : null}
      <label className="flex items-start gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          aria-label="Требовать инструмент финального ответа"
          name="require_explicit_final_answer"
          type="checkbox"
          defaultChecked={Boolean(
            connector?.connection.extra_settings?.require_explicit_final_answer
          )}
          className="mt-1"
        />
        <span>
          Требовать инструмент финального ответа
          <span className="mt-1 block text-xs text-secondary">
            Модель должна завершаться через встроенный инструмент submit_final_answer. Включайте для
            подключений, которые иначе слишком рано возвращают промежуточный текст.
          </span>
        </span>
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="verify_tls"
          type="checkbox"
          defaultChecked={connector?.connection.verify_tls ?? true}
        />
        Проверять TLS
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="is_default"
          type="checkbox"
          defaultChecked={connector?.is_default ?? false}
        />
        По умолчанию для стандартного ассистента
      </label>
    </div>
  );
}

function DocumentationConnectorFields({
  connector,
  isEdit,
}: {
  connector: DocumentationConnectorItem | null;
  isEdit: boolean;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Название">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="platform-docs"
          required
        />
      </Field>
      <Field label="Базовый URL">
        <Input
          name="base_url"
          defaultValue={connector?.connection.base_url ?? ""}
          placeholder="https://docs.company.com"
          required
        />
      </Field>
      <Field label="URL индекса">
        <Input
          name="index_url"
          defaultValue={connector?.connection.index_url ?? ""}
          placeholder="https://docs.company.com/llm-docs-index.json"
        />
      </Field>
      <Field label="Тип аутентификации">
        <NativeSelect name="auth_type" defaultValue={connector?.connection.auth_type ?? "none"}>
          <option value="none">Без аутентификации</option>
          <option value="bearer">Bearer</option>
          <option value="basic">Basic</option>
        </NativeSelect>
      </Field>
      <Field label="Имя пользователя">
        <Input
          name="username"
          defaultValue={connector?.connection.username ?? ""}
          placeholder="user@company.com"
        />
      </Field>
      <Field label={isEdit ? "Токен документации (оставьте пустым, чтобы не менять)" : "Токен документации"}>
        <Input type="password" name="api_token" placeholder="token" />
      </Field>
      <Field label="Применять, когда" fullWidth>
        <Textarea
          name="usage_description"
          defaultValue={connector?.connection.usage_description ?? ""}
          maxLength={1000}
          placeholder="Для управляемой работы с внутренней документацией платформы, онбордингом dbt, регламентами и протоколами решений"
        />
      </Field>
      <Field label="Базовый URL эмбеддингов">
        <Input
          name="embedding_base_url"
          defaultValue={connector?.connection.embedding_base_url ?? ""}
          placeholder="https://llm.example.com"
        />
      </Field>
      <Field label="Модель эмбеддингов">
        <Input
          name="embedding_model"
          defaultValue={connector?.connection.embedding_model ?? "BAAI/bge-small-en-v1.5"}
          required
        />
      </Field>
      <Field label={isEdit ? "Ключ эмбеддингов (оставьте пустым, чтобы не менять)" : "Ключ эмбеддингов"}>
        <Input type="password" name="embedding_api_key" placeholder="token" />
      </Field>
      <Field label="Эндпоинт эмбеддингов">
        <Input
          name="embedding_endpoint_path"
          defaultValue={connector?.connection.embedding_endpoint_path ?? "/v1/embeddings"}
          required
        />
      </Field>
      <Field label="Таймаут эмбеддингов, секунды">
        <Input
          type="number"
          name="embedding_timeout_seconds"
          min={1}
          max={600}
          defaultValue={connector?.connection.embedding_timeout_seconds ?? 120}
        />
      </Field>
      <Field label="URL Qdrant">
        <Input
          name="qdrant_base_url"
          defaultValue={connector?.connection.qdrant_base_url ?? "http://qdrant:6333"}
          required
        />
      </Field>
      <Field label={isEdit ? "Ключ Qdrant (оставьте пустым, чтобы не менять)" : "Ключ Qdrant"}>
        <Input type="password" name="qdrant_api_key" placeholder="необязательный токен" />
      </Field>
      <Field label="Коллекция Qdrant">
        <Input
          name="qdrant_collection_name"
          defaultValue={connector?.connection.qdrant_collection_name ?? "documentation_chunks"}
          required
        />
      </Field>
      <Field label="Размерность вектора">
        <Input
          type="number"
          name="vector_size"
          min={8}
          max={4096}
          defaultValue={connector?.connection.vector_size ?? 1024}
        />
      </Field>
      <Field label="Интервал синхронизации, минуты">
        <Input
          type="number"
          name="sync_interval_minutes"
          min={5}
          max={43200}
          defaultValue={connector?.connection.sync_interval_minutes ?? 1440}
        />
      </Field>
      <Field label="Включённые пути">
        <Input
          name="include_paths"
          defaultValue={connector?.connection.include_paths.join(", ") ?? ""}
          placeholder="/docs/rhetorica"
        />
      </Field>
      <Field label="Исключённые пути">
        <Input
          name="exclude_paths"
          defaultValue={connector?.connection.exclude_paths.join(", ") ?? ""}
          placeholder="/docs/archive"
        />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="verify_tls"
          type="checkbox"
          defaultChecked={connector?.connection.verify_tls ?? true}
        />
        Проверять TLS
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="sync_enabled"
          type="checkbox"
          defaultChecked={connector?.connection.sync_enabled ?? true}
        />
        Синхронизация по расписанию
      </label>
      <div className="rounded-lg border border-border-strong bg-surface px-4 py-3">
        <Switch
          name="visible_to_default_assistant"
          defaultChecked={connector?.visible_to_default_assistant ?? true}
          label="Ассистент по умолчанию"
        />
      </div>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="qdrant_prefer_grpc"
          type="checkbox"
          defaultChecked={connector?.connection.qdrant_prefer_grpc ?? false}
        />
        Предпочитать gRPC
      </label>
    </div>
  );
}

function GitLabConnectorFields({
  connector,
  isEdit,
}: {
  connector: GitLabConnectorItem | null;
  isEdit: boolean;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Название">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="gitlab-main"
          required
        />
      </Field>
      <Field label="Базовый URL">
        <Input
          name="base_url"
          defaultValue={connector?.connection.base_url ?? "https://gitlab.example.com/"}
          placeholder="https://gitlab.example.com/"
          required
        />
      </Field>
      <Field label={isEdit ? "Токен API (оставьте пустым, чтобы не менять)" : "Токен API"}>
        <Input type="password" name="api_token" placeholder="токен read_api или read_repository" />
      </Field>
      <Field label="Лимит поиска">
        <Input
          type="number"
          name="search_limit"
          min={1}
          max={10}
          defaultValue={connector?.connection.search_limit ?? 5}
        />
      </Field>
      <Field label="Когда использовать" fullWidth>
        <Textarea
          name="use_when"
          defaultValue={connector?.connection.use_when ?? ""}
          maxLength={1000}
          placeholder="Для поиска по исходному коду в GitLab только на чтение: поиск файлов, просмотр README, документации и конфигураций, ссылки на точные пути проектов"
          required
        />
      </Field>
      <Field label="Проекты по умолчанию" fullWidth>
        <Input
          name="default_project_paths"
          defaultValue={connector?.connection.default_project_paths.join(", ") ?? ""}
          placeholder="group/project-a, platform/project-b"
        />
      </Field>
      <Field label="Группы по умолчанию" fullWidth>
        <Input
          name="default_group_paths"
          defaultValue={connector?.connection.default_group_paths.join(", ") ?? ""}
          placeholder="analytics, platform"
        />
      </Field>
      <Field label="Максимум токенов на чтение">
        <Input
          type="number"
          name="read_max_tokens"
          min={100}
          max={4000}
          defaultValue={connector?.connection.read_max_tokens ?? 1600}
        />
      </Field>
      <Field label="Максимум записей архива для Coding">
        <Input
          type="number"
          name="coding_archive_max_files"
          min={1}
          max={100000}
          defaultValue={connector?.connection.coding_archive_max_files ?? 20000}
        />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="verify_tls"
          type="checkbox"
          defaultChecked={connector?.connection.verify_tls ?? true}
        />
        Проверять TLS
      </label>
    </div>
  );
}

function McpConnectorFields({
  connector,
  isEdit,
}: {
  connector: McpConnectorItem | null;
  isEdit: boolean;
}) {
  const headerJson = JSON.stringify(connector?.connection.headers ?? {}, null, 2);
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Название">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="mcp-jira-main"
          required
        />
      </Field>
      <Field label="Провайдер">
        <NativeSelect name="provider" defaultValue={connector?.connection.provider ?? "generic"}>
          <option value="jira">Jira</option>
          <option value="confluence">Confluence</option>
          <option value="gitlab">GitLab</option>
          <option value="generic">Универсальный</option>
        </NativeSelect>
      </Field>
      <Field label="URL MCP" fullWidth>
        <Input
          name="url"
          defaultValue={connector?.connection.url ?? ""}
          placeholder="https://mcp.example.com/mcp"
          required
        />
      </Field>
      <Field label="Префикс инструментов">
        <Input
          name="tool_name_prefix"
          defaultValue={connector?.connection.tool_name_prefix ?? ""}
          placeholder="mcp_jira_main"
        />
      </Field>
      <Field label="Включённые группы">
        <Input
          name="enabled_groups"
          defaultValue={connector?.connection.enabled_groups ?? ""}
          placeholder="jira_read,jira_write"
        />
      </Field>
      <Field label="Разрешённые методы MCP" fullWidth>
        <Textarea
          aria-label="Разрешённые методы MCP"
          name="allowed_tool_names"
          defaultValue={(connector?.connection.allowed_tool_names ?? []).join("\n")}
          placeholder={"search_issues\ncreate_issue\nupdate_issue\ncomment_issue"}
          className="font-mono"
        />
        <span className="mt-2 block text-xs text-secondary">
          Точные имена удалённых методов через запятую или с новой строки. Пустое значение означает без ограничений.
        </span>
      </Field>
      <Field label="Подключение LLM">
        <Input
          name="llm_connector_name"
          defaultValue={connector?.connection.llm_connector_name ?? ""}
          placeholder="example-llm"
        />
      </Field>
      <Field label="Таймаут, секунды">
        <Input
          type="number"
          name="timeout_seconds"
          min={1}
          max={300}
          defaultValue={connector?.connection.timeout_seconds ?? 30}
        />
      </Field>
      <Field label="Когда использовать" fullWidth>
        <Textarea
          name="usage_description"
          defaultValue={connector?.connection.usage_description ?? ""}
          maxLength={1000}
          placeholder="Для управляемого доступа к внешним инструментам из Jira, Confluence, GitLab или другого источника MCP"
        />
      </Field>
      <div className="rounded-lg border border-border-strong bg-surface px-4 py-3 md:col-span-2">
        <Switch
          name="visible_to_external_mcp"
          defaultChecked={connector?.visible_to_external_mcp ?? false}
          label="Публиковать через внешний MCP"
        />
        <span className="mt-2 block text-xs text-secondary">
          Публикует разрешённые инструменты этого подключения для аутентифицированных клиентов на /mcp.
        </span>
      </div>
      <Field label="JSON публичных заголовков" fullWidth>
        <Textarea
          name="headers_json"
          defaultValue={headerJson}
          placeholder='{"Enabled-Groups":"core"}'
          className="font-mono"
        />
      </Field>
      <Field label={isEdit ? "JSON секретных заголовков (пусто сохраняет имеющиеся)" : "JSON секретных заголовков"} fullWidth>
        <Textarea
          name="secret_headers_json"
          placeholder='[{"name":"Authorization","value":"token","mode":"bearer"}]'
          className="font-mono"
        />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="verify_tls"
          type="checkbox"
          defaultChecked={connector?.connection.verify_tls ?? true}
        />
        Проверять TLS
      </label>
    </div>
  );
}

function AirflowConnectorFields({
  connector,
  isEdit,
}: {
  connector: AirflowConnectorItem | null;
  isEdit: boolean;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Название">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="airflow-dwh-prod"
          required
        />
      </Field>
      <Field label="Базовый URL">
        <Input
          name="base_url"
          defaultValue={connector?.connection.base_url ?? "https://airflow.example.com/"}
          placeholder="https://airflow.example.com/"
          required
        />
      </Field>
      <Field label="Имя пользователя">
        <Input
          name="username"
          defaultValue={connector?.connection.username ?? ""}
          placeholder="airflow_api_user"
          required
        />
      </Field>
      <Field label={isEdit ? "Пароль (оставьте пустым, чтобы не менять)" : "Пароль"}>
        <Input type="password" name="password" placeholder="Пароль API Airflow" />
      </Field>
      <Field label="Когда использовать" fullWidth>
        <Textarea
          name="use_when"
          defaultValue={
            connector?.connection.use_when ??
            "Для диагностики DAG, расписаний, запусков DAG, состояний и логов задач Airflow для DWH только на чтение, а также ошибок импорта и состояния Airflow"
          }
          maxLength={1000}
          required
        />
      </Field>
      <Field label="Путь API">
        <Input
          name="api_path"
          defaultValue={connector?.connection.api_path ?? "/api/v1"}
          placeholder="/api/v1"
          required
        />
      </Field>
      <Field label="Лимит DAG">
        <Input type="number" name="dag_limit" min={1} max={100} defaultValue={connector?.connection.dag_limit ?? 25} />
      </Field>
      <Field label="Лимит запусков DAG">
        <Input type="number" name="dag_run_limit" min={1} max={100} defaultValue={connector?.connection.dag_run_limit ?? 20} />
      </Field>
      <Field label="Лимит задач">
        <Input type="number" name="task_limit" min={1} max={200} defaultValue={connector?.connection.task_limit ?? 50} />
      </Field>
      <Field label="Максимум символов в логе">
        <Input type="number" name="log_max_chars" min={1000} max={50000} defaultValue={connector?.connection.log_max_chars ?? 8000} />
      </Field>
      <Field label="Таймаут, секунды">
        <Input type="number" name="timeout_seconds" min={1} max={120} defaultValue={connector?.connection.timeout_seconds ?? 20} />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="verify_tls"
          type="checkbox"
          defaultChecked={connector?.connection.verify_tls ?? true}
        />
        Проверять TLS
      </label>
    </div>
  );
}

function KubernetesConnectorFields({
  connector,
  isEdit,
}: {
  connector: KubernetesConnectorItem | null;
  isEdit: boolean;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Название">
        <Input data-autofocus name="name" defaultValue={connector?.name ?? ""} placeholder="k8s-prod" required />
      </Field>
      <Field label="URL API-сервера">
        <Input name="base_url" defaultValue={connector?.connection.base_url ?? ""} placeholder="https://api.cluster.example" required />
      </Field>
      <Field label={isEdit ? "Bearer-токен (оставьте пустым, чтобы не менять)" : "Bearer-токен"} fullWidth>
        <Input type="password" name="bearer_token" placeholder="Токен ServiceAccount только на чтение" />
      </Field>
      <Field label="Когда использовать" fullWidth>
        <Textarea
          name="use_when"
          defaultValue={
            connector?.connection.use_when ??
            "Для диагностики Kubernetes только на чтение: поды, деплойменты, задания, ingress, события, статус раскатки, квоты ресурсов и ограниченные логи подов"
          }
          maxLength={1000}
          required
        />
      </Field>
      <Field label="Разрешённые пространства имён" fullWidth>
        <Input
          name="namespace_allowlist"
          defaultValue={connector?.connection.namespace_allowlist.join(", ") ?? ""}
          placeholder="prod, analytics, rhetorica"
          required
        />
      </Field>
      <Field label="Лимит по умолчанию">
        <Input type="number" name="default_limit" min={1} max={200} defaultValue={connector?.connection.default_limit ?? 25} />
      </Field>
      <Field label="Максимальный лимит">
        <Input type="number" name="max_limit" min={1} max={500} defaultValue={connector?.connection.max_limit ?? 100} />
      </Field>
      <Field label="Максимум символов в логе">
        <Input type="number" name="log_max_chars" min={1000} max={50000} defaultValue={connector?.connection.log_max_chars ?? 8000} />
      </Field>
      <Field label="Таймаут, секунды">
        <Input type="number" name="timeout_seconds" min={1} max={120} defaultValue={connector?.connection.timeout_seconds ?? 20} />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input name="verify_tls" type="checkbox" defaultChecked={connector?.connection.verify_tls ?? true} />
        Проверять TLS
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="visible_to_default_assistant"
          type="checkbox"
          defaultChecked={connector?.visible_to_default_assistant ?? false}
        />
        Виден ассистенту по умолчанию
      </label>
    </div>
  );
}

function VictoriaMetricsConnectorFields({
  connector,
  isEdit,
}: {
  connector: VictoriaMetricsConnectorItem | null;
  isEdit: boolean;
}) {
  const templateValue = JSON.stringify(connector?.connection.templates ?? [], null, 2);
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Название">
        <Input data-autofocus name="name" defaultValue={connector?.name ?? ""} placeholder="vm-prod" required />
      </Field>
      <Field label="Базовый URL">
        <Input name="base_url" defaultValue={connector?.connection.base_url ?? ""} placeholder="https://vmselect.example" required />
      </Field>
      <Field label="Тип аутентификации">
        <NativeSelect name="auth_type" defaultValue={connector?.connection.auth_type ?? "none"}>
          <option value="none">Без аутентификации</option>
          <option value="bearer">Bearer</option>
        </NativeSelect>
      </Field>
      <Field label={isEdit ? "Bearer-токен (оставьте пустым, чтобы не менять)" : "Bearer-токен"}>
        <Input type="password" name="bearer_token" placeholder="Необязательный токен API" />
      </Field>
      <Field label="Когда использовать" fullWidth>
        <Textarea
          name="use_when"
          defaultValue={
            connector?.connection.use_when ??
            "Для диагностики VictoriaMetrics только на чтение и только по шаблонам метрик, настроенным администратором"
          }
          maxLength={1000}
          required
        />
      </Field>
      <Field label="Путь запроса">
        <Input name="query_path" defaultValue={connector?.connection.query_path ?? "/prometheus/api/v1/query"} required />
      </Field>
      <Field label="Путь запроса диапазона">
        <Input name="query_range_path" defaultValue={connector?.connection.query_range_path ?? "/prometheus/api/v1/query_range"} required />
      </Field>
      <Field label="JSON шаблонов" fullWidth>
        <Textarea
          name="templates"
          defaultValue={templateValue}
          rows={12}
          placeholder='[{"template_id":"pod_cpu","title":"Pod CPU","use_when":"CPU by pod","query_template":"rate(container_cpu_usage_seconds_total{namespace=\"$namespace\"}[5m])","mode":"range","allowed_filters":["namespace"],"default_step":"60s","max_range_seconds":86400,"unit":"cores","result_limit":20}]'
        />
      </Field>
      <Field label="Максимум серий">
        <Input type="number" name="max_series" min={1} max={200} defaultValue={connector?.connection.max_series ?? 20} />
      </Field>
      <Field label="Максимум точек данных">
        <Input type="number" name="max_datapoints" min={1} max={5000} defaultValue={connector?.connection.max_datapoints ?? 300} />
      </Field>
      <Field label="Таймаут, секунды">
        <Input type="number" name="timeout_seconds" min={1} max={120} defaultValue={connector?.connection.timeout_seconds ?? 20} />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input name="verify_tls" type="checkbox" defaultChecked={connector?.connection.verify_tls ?? true} />
        Проверять TLS
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="visible_to_default_assistant"
          type="checkbox"
          defaultChecked={connector?.visible_to_default_assistant ?? false}
        />
        Виден ассистенту по умолчанию
      </label>
    </div>
  );
}

function MattermostConnectorFields({
  connector,
  isEdit,
  agents,
}: {
  connector: MattermostConnectorItem | null;
  isEdit: boolean;
  agents: AgentProfile[];
}) {
  const activeAgents = agents.filter((agent) => agent.is_active);
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Название">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="tag-main"
          required
        />
      </Field>
      <Field label="Базовый URL">
        <Input
          name="base_url"
          defaultValue={connector?.connection.base_url ?? "https://mattermost.example.com"}
          placeholder="https://mattermost.example.com"
          required
        />
      </Field>
      <Field label="Имя пользователя бота">
        <Input
          name="bot_username"
          defaultValue={connector?.connection.bot_username ?? ""}
          placeholder="rhetorica"
        />
      </Field>
      <Field label={isEdit ? "Токен бота (оставьте пустым, чтобы не менять)" : "Токен бота"}>
        <Input type="password" name="bot_token" placeholder="bot-token" />
      </Field>
      <Field label="Ассистент">
        <NativeSelect name="agent_id" defaultValue={connector?.agent_id ?? ""}>
          <option value="">Ассистент по умолчанию</option>
          {activeAgents.map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.title}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="verify_tls"
          type="checkbox"
          defaultChecked={connector?.connection.verify_tls ?? true}
        />
        Проверять TLS
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="listen_enabled"
          type="checkbox"
          defaultChecked={connector?.connection.listen_enabled ?? true}
        />
        Слушать Tag
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="ambient_listen_enabled"
          type="checkbox"
          defaultChecked={connector?.connection.ambient_listen_enabled ?? false}
        />
        Фоновый режим
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-border-strong bg-surface px-4 py-3 text-sm text-primary">
        <input
          name="send_sql_enabled"
          type="checkbox"
          defaultChecked={connector?.connection.send_sql_enabled ?? false}
        />
        Отправлять сгенерированный SQL
      </label>
      <Field label="ID каналов фонового режима" fullWidth>
        <Textarea
          name="ambient_channel_ids"
          defaultValue={(connector?.connection.ambient_channel_ids ?? []).join("\n")}
          placeholder={"channel-id-1\nchannel-id-2"}
          rows={3}
        />
      </Field>
      <Field label="Промпт решения в фоновом режиме" fullWidth>
        <Textarea
          name="ambient_decision_prompt"
          defaultValue={connector?.connection.ambient_decision_prompt ?? ""}
          placeholder="Подключаться только тогда, когда обсуждение запрашивает аналитическую помощь, SQL, метрики, дашборды или диагностику DWH"
          rows={5}
        />
      </Field>
    </div>
  );
}
