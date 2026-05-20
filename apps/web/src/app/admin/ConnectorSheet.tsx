"use client";

import * as React from "react";

import { Alert, Badge, Button, Input, Sheet, Switch } from "@rhetorica/ui";

import type {
  AnyConnectorItem,
  ConnectorType,
  ConfluenceConnectorItem,
  DatabaseConnectorItem,
  JiraConnectorItem,
  LlmConnectorItem,
  MattermostConnectorItem,
  OpenMetadataConnectorItem,
} from "./types";
import { getConnectorTypeLabel } from "./adminUtils";

type ConnectorSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connectorType: ConnectorType | null;
  editingConnector: AnyConnectorItem | null;
  onSubmit: (formData: FormData) => Promise<void>;
  saving: boolean;
  error: string | null;
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
      <span className="mb-2 block text-sm font-medium text-[#c7d8f4]">{label}</span>
      {children}
    </label>
  );
}

function NativeSelect(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className="flex h-10 w-full rounded-lg border border-[#2e3d59] bg-[#090e1a] px-3 py-2 text-sm text-[#edf3ff] outline-none transition focus:border-transparent focus:ring-2 focus:ring-[#5da8ff]"
    />
  );
}

export function ConnectorSheet({
  open,
  onOpenChange,
  connectorType,
  editingConnector,
  onSubmit,
  saving,
  error,
}: ConnectorSheetProps) {
  const isEdit = editingConnector != null;
  const [activeType, setActiveType] = React.useState<ConnectorType>(connectorType ?? "database");

  React.useEffect(() => {
    if (!open) {
      return;
    }

    setActiveType(connectorType ?? "database");
  }, [connectorType, open]);

  const title = `${isEdit ? "Edit" : "Add"} ${getConnectorTypeLabel(activeType)} connector`;

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description="Configuration stays in place while the list updates optimistically. Secrets are preserved unless you replace them."
    >
      <div className="space-y-5">
        <div className="flex flex-wrap gap-2">
          {(["database", "llm", "metadata", "confluence", "jira", "mattermost"] as ConnectorType[]).map((type) => (
            <button
              key={type}
              type="button"
              disabled={isEdit}
              onClick={() => setActiveType(type)}
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm transition ${
                activeType === type
                  ? "border-[#5da8ff]/40 bg-[#5da8ff]/16 text-[#eef4ff]"
                  : "border-[#263750] bg-[#0b1322] text-[#9bb0cf] hover:border-[#35537a] hover:text-white"
              } ${isEdit ? "cursor-not-allowed opacity-70" : ""}`}
            >
              {getConnectorTypeLabel(type)}
            </button>
          ))}
        </div>

        {error ? (
          <Alert
            variant="danger"
            title="Save failed"
            description={error}
          />
        ) : null}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void onSubmit(new FormData(event.currentTarget));
          }}
          className="space-y-5"
        >
          <input type="hidden" name="connector_id" value={editingConnector?.id ?? ""} />
          <input type="hidden" name="connector_type" value={activeType} />
          <div className="rounded-[18px] border border-[#23324e] bg-[#0b1322] px-4 py-3">
            <Switch
              name="is_active"
              defaultChecked={editingConnector?.is_active ?? true}
              label="Active"
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

          {activeType === "metadata" ? (
            <MetadataConnectorFields
              connector={editingConnector as OpenMetadataConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          {activeType === "confluence" ? (
            <ConfluenceConnectorFields
              connector={editingConnector as ConfluenceConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          {activeType === "jira" ? (
            <JiraConnectorFields
              connector={editingConnector as JiraConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          {activeType === "mattermost" ? (
            <MattermostConnectorFields
              connector={editingConnector as MattermostConnectorItem | null}
              isEdit={isEdit}
            />
          ) : null}

          <div className="rounded-[24px] border border-[#1f2d45] bg-[#0a111f]/90 p-4">
            <div className="flex items-center gap-2">
              <Badge variant="info">{isEdit ? "Updating" : "Creating"}</Badge>
              <p className="text-sm text-[#c7d8f4]">
                Keep the config dense. Admin users need speed, not ceremony.
              </p>
            </div>
          </div>

          <div className="flex items-center justify-end gap-3">
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              className="border border-[#24344f] bg-[#0b1322] text-[#d7e6ff] hover:bg-[#132033]"
            >
              Cancel
            </Button>
            <Button type="submit" isLoading={saving}>
              {isEdit ? "Save connector" : "Create connector"}
            </Button>
          </div>
        </form>
      </div>
    </Sheet>
  );
}

function DatabaseConnectorFields({
  connector,
  isEdit,
}: {
  connector: DatabaseConnectorItem | null;
  isEdit: boolean;
}) {
  const queryTimeoutSeconds =
    connector?.connection.extra_settings?.query_timeout_seconds ??
    connector?.connection.extra_settings?.query_timeout_sec ??
    connector?.connection.extra_settings?.sql_timeout_seconds ??
    "";

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Field label="Name">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="clickhouse-prod"
          required
        />
      </Field>
      <Field label="Engine">
        <NativeSelect name="engine" defaultValue={connector?.connection.engine ?? "clickhouse"}>
          <option value="clickhouse">ClickHouse</option>
          <option value="greenplum">Greenplum</option>
          <option value="impala">Impala</option>
          <option value="trino">Trino</option>
        </NativeSelect>
      </Field>
      <Field label="Host">
        <Input name="host" defaultValue={connector?.connection.host ?? ""} placeholder="host.example.com" required />
      </Field>
      <Field label="Port">
        <Input
          type="number"
          name="port"
          defaultValue={connector?.connection.port ?? 8123}
          min={1}
          max={65535}
          required
        />
      </Field>
      <Field label="Username">
        <Input name="username" defaultValue={connector?.connection.username ?? ""} placeholder="analyst" required />
      </Field>
      <Field label={isEdit ? "Password (leave blank to keep)" : "Password"}>
        <Input type="password" name="password" placeholder="••••••••" />
      </Field>
      <Field label="Database">
        <Input name="database" defaultValue={connector?.connection.database ?? ""} placeholder="analytics" />
      </Field>
      <Field label="Catalog">
        <Input name="catalog" defaultValue={connector?.connection.catalog ?? ""} placeholder="hive" />
      </Field>
      <Field label="Schema">
        <Input name="schema" defaultValue={connector?.connection.schema ?? ""} placeholder="dm" />
      </Field>
      <Field label="Auth mechanism">
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
      <Field label="Retries">
        <Input
          type="number"
          name="retries"
          defaultValue={String(connector?.connection.extra_settings?.retries ?? 1)}
          min={0}
          max={20}
        />
      </Field>
      <Field label="CA cert path">
        <Input
          name="ca_cert"
          defaultValue={String(connector?.connection.extra_settings?.ca_cert ?? "")}
          placeholder="/etc/ssl/certs/impala.pem"
        />
      </Field>
      <Field label="HTTP path">
        <Input
          name="http_path"
          defaultValue={String(connector?.connection.extra_settings?.http_path ?? "")}
          placeholder="cliservice"
        />
      </Field>
      <Field label="SQL timeout sec">
        <Input
          type="number"
          name="query_timeout_seconds"
          defaultValue={String(queryTimeoutSeconds)}
          min={1}
          max={900}
        />
      </Field>
      <Field label="Connect timeout sec">
        <Input
          type="number"
          name="timeout"
          defaultValue={String(connector?.connection.extra_settings?.timeout ?? "")}
          min={1}
          max={300}
        />
      </Field>
      <Field label="OpenMetadata service">
        <Input
          name="metadata_service"
          defaultValue={String(connector?.connection.extra_settings?.metadata_service ?? "")}
          placeholder="clickhouse_prod"
        />
      </Field>
      <Field label="OpenMetadata database">
        <Input
          name="metadata_database"
          defaultValue={String(connector?.connection.extra_settings?.metadata_database ?? "")}
          placeholder="analytics"
        />
      </Field>
      <Field label="OpenMetadata schema">
        <Input
          name="metadata_schema"
          defaultValue={String(connector?.connection.extra_settings?.metadata_schema ?? "")}
          placeholder="dm"
        />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-[#23324e] bg-[#0b1322] px-4 py-3 text-sm text-[#d7e6ff]">
        <input name="secure" type="checkbox" defaultChecked={connector?.connection.secure} />
        Use TLS
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-[#23324e] bg-[#0b1322] px-4 py-3 text-sm text-[#d7e6ff]">
        <input name="verify" type="checkbox" defaultChecked={connector?.connection.verify ?? true} />
        Verify TLS certificate
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-[#23324e] bg-[#0b1322] px-4 py-3 text-sm text-[#d7e6ff]">
        <input
          name="use_http_transport"
          type="checkbox"
          defaultChecked={Boolean(connector?.connection.extra_settings?.use_http_transport)}
        />
        Use HTTP transport
      </label>
    </div>
  );
}

function LlmConnectorFields({
  connector,
  isEdit,
}: {
  connector: LlmConnectorItem | null;
  isEdit: boolean;
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Field label="Name">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="openrouter-main"
          required
        />
      </Field>
      <Field label="Base URL">
        <Input
          name="base_url"
          defaultValue={connector?.connection.base_url ?? ""}
          placeholder="https://openrouter.ai/api/v1"
          required
        />
      </Field>
      <Field label="Model">
        <Input
          name="model"
          defaultValue={connector?.connection.model ?? ""}
          placeholder="openai/gpt-4.1"
          required
        />
      </Field>
      <Field label={isEdit ? "API key (leave blank to keep)" : "API key"}>
        <Input type="password" name="api_key" placeholder="sk-..." />
      </Field>
      <Field label="Endpoint path">
        <Input
          name="endpoint_path"
          defaultValue={connector?.connection.endpoint_path ?? "/chat/completions"}
          required
        />
      </Field>
      <Field label="Temperature">
        <Input
          type="number"
          name="temperature"
          step="0.1"
          min={0}
          max={2}
          defaultValue={connector?.connection.temperature ?? 0.1}
        />
      </Field>
      <Field label="Max tokens">
        <Input
          type="number"
          name="max_tokens"
          min={1}
          max={32000}
          defaultValue={connector?.connection.max_tokens ?? 1200}
        />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-[#23324e] bg-[#0b1322] px-4 py-3 text-sm text-[#d7e6ff]">
        <input
          name="verify_tls"
          type="checkbox"
          defaultChecked={connector?.connection.verify_tls ?? true}
        />
        Verify TLS
      </label>
    </div>
  );
}

function MetadataConnectorFields({
  connector,
  isEdit,
}: {
  connector: OpenMetadataConnectorItem | null;
  isEdit: boolean;
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Field label="Name">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="openmetadata-main"
          required
        />
      </Field>
      <Field label="Base URL">
        <Input
          name="base_url"
          defaultValue={connector?.connection.base_url ?? ""}
          placeholder="http://openmetadata.local:8585"
          required
        />
      </Field>
      <Field label={isEdit ? "API token (leave blank to keep)" : "API token"}>
        <Input type="password" name="api_token" placeholder="jwt-token" />
      </Field>
      <Field label="MCP URL">
        <Input
          name="mcp_tables_url"
          defaultValue={connector?.connection.mcp_tables_url ?? ""}
          placeholder="https://openmetadata.local:8585/mcp"
        />
      </Field>
      <Field label="Default service">
        <Input name="service_name" defaultValue={connector?.connection.service_name ?? ""} placeholder="clickhouse_prod" />
      </Field>
      <Field label="Default database">
        <Input name="database_name" defaultValue={connector?.connection.database_name ?? ""} placeholder="analytics" />
      </Field>
      <Field label="Default schema">
        <Input name="schema_name" defaultValue={connector?.connection.schema_name ?? ""} placeholder="dm" />
      </Field>
      <Field label="Table limit">
        <Input
          type="number"
          name="table_limit"
          min={1}
          max={2000}
          defaultValue={connector?.connection.table_limit ?? 200}
        />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-[#23324e] bg-[#0b1322] px-4 py-3 text-sm text-[#d7e6ff]">
        <input
          name="verify_tls"
          type="checkbox"
          defaultChecked={connector?.connection.verify_tls ?? true}
        />
        Verify TLS
      </label>
    </div>
  );
}

function ConfluenceConnectorFields({
  connector,
  isEdit,
}: {
  connector: ConfluenceConnectorItem | null;
  isEdit: boolean;
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Field label="Name">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="confluence-main"
          required
        />
      </Field>
      <Field label="Base URL">
        <Input
          name="base_url"
          defaultValue={connector?.connection.base_url ?? ""}
          placeholder="https://confluence.company.com"
          required
        />
      </Field>
      <Field label="Username">
        <Input
          name="username"
          defaultValue={connector?.connection.username ?? ""}
          placeholder="user@company.com"
        />
      </Field>
      <Field label={isEdit ? "API token (leave blank to keep)" : "API token"}>
        <Input type="password" name="api_token" placeholder="token" />
      </Field>
      <Field label="Auth type">
        <NativeSelect name="auth_type" defaultValue={connector?.connection.auth_type ?? "bearer"}>
          <option value="bearer">Bearer</option>
          <option value="basic">Basic</option>
        </NativeSelect>
      </Field>
      <Field label="Space key">
        <Input name="space_key" defaultValue={connector?.connection.space_key ?? ""} placeholder="DOCS" />
      </Field>
      <Field label="Page limit">
        <Input
          type="number"
          name="page_limit"
          min={1}
          max={50}
          defaultValue={connector?.connection.page_limit ?? 10}
        />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-[#23324e] bg-[#0b1322] px-4 py-3 text-sm text-[#d7e6ff]">
        <input
          name="verify_tls"
          type="checkbox"
          defaultChecked={connector?.connection.verify_tls ?? true}
        />
        Verify TLS
      </label>
    </div>
  );
}

function JiraConnectorFields({
  connector,
  isEdit,
}: {
  connector: JiraConnectorItem | null;
  isEdit: boolean;
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Field label="Name">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="jira-main"
          required
        />
      </Field>
      <Field label="Base URL">
        <Input
          name="base_url"
          defaultValue={connector?.connection.base_url ?? ""}
          placeholder="https://jira.company.com"
          required
        />
      </Field>
      <Field label="Username">
        <Input
          name="username"
          defaultValue={connector?.connection.username ?? ""}
          placeholder="jira-user"
        />
      </Field>
      <Field label={isEdit ? "API token (leave blank to keep)" : "API token"}>
        <Input type="password" name="api_token" placeholder="token" />
      </Field>
      <Field label="Auth type">
        <NativeSelect name="auth_type" defaultValue={connector?.connection.auth_type ?? "bearer"}>
          <option value="bearer">Bearer</option>
          <option value="basic">Basic</option>
        </NativeSelect>
      </Field>
      <Field label="Default projects">
        <Input
          name="project_keys"
          defaultValue={connector?.connection.project_keys.join(", ") ?? ""}
          placeholder="DM, OPS"
        />
      </Field>
      <Field label="Issue limit">
        <Input
          type="number"
          name="issue_limit"
          min={1}
          max={50}
          defaultValue={connector?.connection.issue_limit ?? 10}
        />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-[#23324e] bg-[#0b1322] px-4 py-3 text-sm text-[#d7e6ff]">
        <input
          name="verify_tls"
          type="checkbox"
          defaultChecked={connector?.connection.verify_tls ?? true}
        />
        Verify TLS
      </label>
    </div>
  );
}

function MattermostConnectorFields({
  connector,
  isEdit,
}: {
  connector: MattermostConnectorItem | null;
  isEdit: boolean;
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Field label="Name">
        <Input
          data-autofocus
          name="name"
          defaultValue={connector?.name ?? ""}
          placeholder="tag-main"
          required
        />
      </Field>
      <Field label="Base URL">
        <Input
          name="base_url"
          defaultValue={connector?.connection.base_url ?? "https://chat.example.com"}
          placeholder="https://chat.example.com"
          required
        />
      </Field>
      <Field label="Bot username">
        <Input
          name="bot_username"
          defaultValue={connector?.connection.bot_username ?? ""}
          placeholder="rhetorica"
        />
      </Field>
      <Field label={isEdit ? "Bot token (leave blank to keep)" : "Bot token"}>
        <Input type="password" name="bot_token" placeholder="bot-token" />
      </Field>
      <label className="flex items-center gap-3 rounded-2xl border border-[#23324e] bg-[#0b1322] px-4 py-3 text-sm text-[#d7e6ff]">
        <input
          name="verify_tls"
          type="checkbox"
          defaultChecked={connector?.connection.verify_tls ?? true}
        />
        Verify TLS
      </label>
      <label className="flex items-center gap-3 rounded-2xl border border-[#23324e] bg-[#0b1322] px-4 py-3 text-sm text-[#d7e6ff]">
        <input
          name="listen_enabled"
          type="checkbox"
          defaultChecked={connector?.connection.listen_enabled ?? true}
        />
        Listen to Tag
      </label>
    </div>
  );
}
