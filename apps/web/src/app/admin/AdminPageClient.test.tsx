import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@rhetorica/ui";

import { AdminPageClient } from "./AdminPageClient";
import type { DatabaseConnectorItem, LlmConnectorItem, McpConnectorItem } from "./types";

vi.mock("@/features/scenarios/scenariosApi", () => ({
  archiveScenario: vi.fn(),
}));

function renderAdminPage({
  isSystemAdmin = true,
  databaseConnectors = [],
  llmConnectors = [],
  mcpConnectors = [],
}: {
  isSystemAdmin?: boolean;
  databaseConnectors?: DatabaseConnectorItem[];
  llmConnectors?: LlmConnectorItem[];
  mcpConnectors?: McpConnectorItem[];
} = {}) {
  return render(
    <ToastProvider>
      <AdminPageClient
        isSystemAdmin={isSystemAdmin}
        databaseConnectors={databaseConnectors}
        llmConnectors={llmConnectors}
        documentationConnectors={[]}
        gitlabConnectors={[]}
        mcpConnectors={mcpConnectors}
        airflowConnectors={[]}
        kubernetesConnectors={[]}
        victoriametricsConnectors={[]}
        mattermostConnectors={[]}
        pendingInsights={[
          {
            id: "review-1",
            workspace_id: "default",
            conversation_id: "conv-1",
            message_id: "msg-1",
            execution_id: "exec-1",
            summary: "Check suspicious revenue spike",
            article_markdown: "# Draft",
            answer_excerpt: "Revenue jumped 17% WoW",
            generated_sql: "select 1",
            context_tables: ["dm_revenue_daily"],
            metadata_sources: ["mcp"],
            keywords: ["revenue", "wow"],
            llm_confidence: 0.92,
            llm_reason: "Strong metadata and SQL evidence",
            likes_count: 3,
            verification_status: "pending",
            human_verified: false,
            verified_by: null,
            verified_at: null,
            created_at: "2026-04-05T12:00:00Z",
            updated_at: "2026-04-05T12:00:00Z",
          },
        ]}
        scenarios={[]}
        workspaceSkills={[]}
        localUsers={[
          {
            id: "user-1",
            workspace_id: "default",
            login: "admin",
            role: "admin",
            is_active: true,
            auth_provider: "local",
            auth_subject: "local-user:1",
            display_name: "Admin",
            api_rate_limit_per_hour: null,
            created_at: "2026-04-05T11:00:00Z",
            updated_at: "2026-04-05T11:00:00Z",
          },
          {
            id: "user-2",
            workspace_id: "default",
            login: "victim",
            role: "analyst",
            is_active: true,
            auth_provider: "local",
            auth_subject: "local-user:2",
            display_name: "Victim",
            api_rate_limit_per_hour: null,
            created_at: "2026-04-05T12:00:00Z",
            updated_at: "2026-04-05T12:00:00Z",
          },
        ]}
        currentUserId="user-1"
        workspaceId="default"
        workspaces={[]}
        workspaceMemberships={[]}
        workspaceResourceGrants={[]}
        workspaceUsers={[]}
        workspaceResourceOptions={[]}
      />
    </ToastProvider>
  );
}

describe("AdminPageClient", () => {
  it("limits workspace owners to local connectors and skills", async () => {
    renderAdminPage({ isSystemAdmin: false });

    expect(screen.getByRole("heading", { name: "Ресурсы рабочего пространства" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Настройки/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Доступ/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Проверка/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Управление/ }));
    expect((await screen.findAllByRole("button", { name: "Добавить навык" })).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Добавить агента" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Добавить официальный сценарий" })).not.toBeInTheDocument();
  });

  it("opens the workflow creator with official scope from both governance actions", async () => {
    renderAdminPage();
    const tab = screen.getAllByRole("button", { name: /Управление/ }).find(button => button.getAttribute("aria-pressed") !== null)!;
    fireEvent.click(tab);
    expect(await screen.findByRole("link", { name: "Добавить официальный сценарий" })).toHaveAttribute("href", "/scenarios/new?scope=official");
    expect(screen.getByRole("link", { name: "Создать сценарий" })).toHaveAttribute("href", "/scenarios/new?scope=official");
    expect(screen.queryByLabelText("Prompt template")).not.toBeInTheDocument();
  });

  it("renders overview readiness and blockers", () => {
    renderAdminPage();

    expect(screen.getByText("Готовность")).toBeInTheDocument();
    expect(screen.getByText("Нет активного подключения к базе данных")).toBeInTheDocument();
    expect(screen.getByText("Очистить очередь проверки")).toBeInTheDocument();
  });

  it("renders existing mcp connectors without an allowed tool list", async () => {
    const user = userEvent.setup();

    renderAdminPage({
      mcpConnectors: [
        {
          id: "mcp-legacy",
          name: "mcp-legacy",
          workspace_id: "default",
          is_active: true,
          visible_to_external_mcp: false,
          connection: {
            provider: "generic",
            transport: "streamable-http",
            url: "https://mcp.example.com/mcp",
            verify_tls: true,
            headers: {},
            enabled_groups: null,
            timeout_seconds: 30,
            tool_name_prefix: null,
            llm_connector_name: null,
            usage_description: null,
          },
          has_secret_headers: false,
          secret_header_names: [],
          tool_count: 0,
          last_sync_status: null,
          last_sync_error: null,
          last_sync_at: null,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        },
      ],
    });

    await user.click(screen.getByRole("button", { name: /Настройки/ }));

    expect((await screen.findAllByText("mcp-legacy")).length).toBeGreaterThan(0);
  });

  it("creates a local user from the access section", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "user-3",
          workspace_id: "default",
          login: "new-analyst",
          role: "analyst",
          is_active: true,
          auth_provider: "local",
          auth_subject: "local-user:3",
          display_name: "New Analyst",
          api_rate_limit_per_hour: null,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Доступ/ }));
    const createUserSection = within(
      (await screen.findByLabelText("Логин")).closest("form") as HTMLElement
    );
    await user.type(createUserSection.getByLabelText("Логин"), "new-analyst");
    await user.type(createUserSection.getByLabelText("Пароль"), "strong-pass-123");
    await user.selectOptions(createUserSection.getByLabelText("Роль"), "analyst");
    await user.type(createUserSection.getByLabelText("Отображаемое имя"), "New Analyst");
    await user.click(screen.getByRole("button", { name: "Создать пользователя" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/users",
        expect.objectContaining({
          method: "POST",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      login: "new-analyst",
      password: "strong-pass-123",
      role: "analyst",
      display_name: "New Analyst",
    });

    await waitFor(() => {
      expect(screen.getByText("new-analyst")).toBeInTheDocument();
    });
  });

  it("deletes a local user through confirm dialog", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Доступ/ }));
    await user.click((await screen.findAllByRole("button", { name: "Удалить" }))[1]!);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Удалить" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/users/user-2",
        expect.objectContaining({ method: "DELETE", cache: "no-store" })
      );
    });

    await waitFor(() => {
      expect(screen.queryByText("Victim")).not.toBeInTheDocument();
    });
  });

  it("configures, resets, and disables a per-user API limit", async () => {
    const user = userEvent.setup();
    const limitedUser = {
      id: "user-2",
      workspace_id: "default",
      login: "victim",
      role: "analyst",
      is_active: true,
      auth_provider: "local",
      auth_subject: "local-user:2",
      display_name: "Victim",
      api_rate_limit_per_hour: 20,
      created_at: "2026-04-05T12:00:00Z",
      updated_at: "2026-04-05T12:00:00Z",
    };
    const unlimitedUser = { ...limitedUser, api_rate_limit_per_hour: null };
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(limitedUser), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(limitedUser), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(unlimitedUser), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();
    await user.click(screen.getByRole("button", { name: /Доступ/ }));
    await user.click(
      await screen.findByRole("button", { name: "Настроить лимит API для victim" })
    );
    let dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByLabelText("Ограничить запросы API для этой учётной записи"));
    await user.clear(within(dialog).getByLabelText("Запросов в час"));
    await user.type(within(dialog).getByLabelText("Запросов в час"), "20");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить лимит" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenNthCalledWith(
        1,
        "/api/admin/users/user-2/rate-limit",
        expect.objectContaining({ method: "PUT", cache: "no-store" })
      );
    });
    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      requests_per_hour: 20,
    });
    expect(await screen.findByText("20 / час")).toBeInTheDocument();

    await user.click(
      await screen.findByRole("button", { name: "Настроить лимит API для victim" })
    );
    dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Сбросить использование" }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenNthCalledWith(
        2,
        "/api/admin/users/user-2/rate-limit/reset",
        expect.objectContaining({ method: "POST", cache: "no-store" })
      );
    });

    await user.click(within(dialog).getByLabelText("Ограничить запросы API для этой учётной записи"));
    await user.click(within(dialog).getByRole("button", { name: "Сохранить лимит" }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenNthCalledWith(
        3,
        "/api/admin/users/user-2/rate-limit",
        expect.objectContaining({ method: "DELETE", cache: "no-store" })
      );
    });
    await waitFor(() => {
      expect(screen.queryByText("20 / час")).not.toBeInTheDocument();
    });
  });

  it("validates and preserves the rate-limit dialog after an API error", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "rate_limit_unavailable" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();
    await user.click(screen.getByRole("button", { name: /Доступ/ }));
    await user.click(
      await screen.findByRole("button", { name: "Настроить лимит API для victim" })
    );
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByLabelText("Ограничить запросы API для этой учётной записи"));
    await user.clear(within(dialog).getByLabelText("Запросов в час"));
    await user.type(within(dialog).getByLabelText("Запросов в час"), "0");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить лимит" }));
    expect(within(dialog).getByText(/целое положительное число/)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();

    await user.clear(within(dialog).getByLabelText("Запросов в час"));
    await user.type(within(dialog).getByLabelText("Запросов в час"), "20");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить лимит" }));
    expect(await within(dialog).findByText("rate_limit_unavailable")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("reviews and removes a pending insight optimistically", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Проверка/ }));
    await user.click(await screen.findByRole("button", { name: "Подтвердить" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/rag-insights/review-1/approve",
        expect.objectContaining({ method: "POST", cache: "no-store" })
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Очередь пуста")).toBeInTheDocument();
    });
  });

  it("keeps queue panel copy inside the card bounds", async () => {
    const user = userEvent.setup();

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Проверка/ }));

    const queueTitle = await screen.findByText("Очередь");
    const queueDescription = screen.getByText("Ожидает проверки: 1 инсайт");

    expect(queueTitle).toHaveClass("break-words");
    expect(queueDescription).toHaveClass("break-words");
  });

  it("creates a mattermost connector with ambient listening settings", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "mattermost-1",
          name: "tag-main",
          workspace_id: "default",
          is_active: true,
          agent_id: null,
          has_bot_token: true,
          connection: {
            provider: "mattermost",
            base_url: "https://mattermost.example.com",
            bot_token: null,
            bot_username: "rhetorica",
            verify_tls: true,
            listen_enabled: true,
            send_sql_enabled: true,
            ambient_listen_enabled: true,
            ambient_channel_ids: ["channel-1", "channel-2"],
            ambient_decision_prompt: "Join only analytics discussions",
            extra_settings: {},
          },
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click((await screen.findAllByRole("button", { name: "Добавить подключение" }))[8]!);

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "tag-main");
    await user.clear(within(dialog).getByLabelText("Базовый URL"));
    await user.type(within(dialog).getByLabelText("Базовый URL"), "https://mattermost.example.com");
    await user.type(within(dialog).getByLabelText("Имя пользователя бота"), "rhetorica");
    await user.type(within(dialog).getByLabelText("Токен бота"), "secret-token");
    await user.click(within(dialog).getByLabelText("Фоновый режим"));
    await user.click(within(dialog).getByLabelText("Отправлять сгенерированный SQL"));
    await user.type(within(dialog).getByLabelText("ID каналов фонового режима"), "channel-1\nchannel-2");
    await user.type(
      within(dialog).getByLabelText("Промпт решения в фоновом режиме"),
      "Join only analytics discussions"
    );
    await user.click(within(dialog).getByRole("button", { name: "Создать подключение" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/mattermost-connectors",
        expect.objectContaining({
          method: "POST",
          cache: "no-store",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      name: "tag-main",
      workspace_id: "default",
      is_active: true,
      agent_id: null,
      connection: {
        provider: "mattermost",
        base_url: "https://mattermost.example.com",
        bot_token: "secret-token",
        bot_username: "rhetorica",
        verify_tls: true,
        listen_enabled: true,
        send_sql_enabled: true,
        ambient_listen_enabled: true,
        ambient_channel_ids: ["channel-1", "channel-2"],
        ambient_decision_prompt: "Join only analytics discussions",
        extra_settings: {},
      },
    });

    await waitFor(() => {
      expect(within(screen.getByRole("main")).getByText("tag-main")).toBeInTheDocument();
    });
  });

  it("creates a gitlab connector from setup", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "gitlab-1",
          name: "gitlab-main",
          workspace_id: "default",
          is_active: true,
          connection: {
            provider: "gitlab",
            base_url: "https://gitlab.example.com",
            api_token: null,
            use_when: "Use for read-only source code lookup",
            verify_tls: true,
            default_project_paths: ["group/app"],
            default_group_paths: ["platform"],
            search_limit: 7,
            read_max_tokens: 1200,
            coding_archive_max_files: 25000,
            extra_settings: {},
          },
          has_api_token: true,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click((await screen.findAllByRole("button", { name: "Добавить подключение" }))[3]!);

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "gitlab-main");
    await user.clear(within(dialog).getByLabelText("Базовый URL"));
    await user.type(within(dialog).getByLabelText("Базовый URL"), "https://gitlab.example.com/");
    await user.type(within(dialog).getByLabelText("Токен API"), "secret-token");
    await user.type(within(dialog).getByLabelText("Когда использовать"), "Use for read-only source code lookup");
    await user.type(within(dialog).getByLabelText("Проекты по умолчанию"), "group/app");
    await user.type(within(dialog).getByLabelText("Группы по умолчанию"), "platform");
    await user.clear(within(dialog).getByLabelText("Лимит поиска"));
    await user.type(within(dialog).getByLabelText("Лимит поиска"), "7");
    await user.clear(within(dialog).getByLabelText("Максимум токенов на чтение"));
    await user.type(within(dialog).getByLabelText("Максимум токенов на чтение"), "1200");
    await user.clear(within(dialog).getByLabelText("Максимум записей архива для Coding"));
    await user.type(within(dialog).getByLabelText("Максимум записей архива для Coding"), "25000");
    await user.click(within(dialog).getByRole("button", { name: "Создать подключение" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/gitlab-connectors",
        expect.objectContaining({
          method: "POST",
          cache: "no-store",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      name: "gitlab-main",
      workspace_id: "default",
      is_active: true,
      connection: {
        provider: "gitlab",
        base_url: "https://gitlab.example.com/",
        api_token: "secret-token",
        use_when: "Use for read-only source code lookup",
        verify_tls: true,
        default_project_paths: ["group/app"],
        default_group_paths: ["platform"],
        search_limit: 7,
        read_max_tokens: 1200,
        coding_archive_max_files: 25000,
        extra_settings: {},
      },
    });

    await waitFor(() => {
      expect(within(screen.getByRole("main")).getByText("gitlab-main")).toBeInTheDocument();
    });
  });

  it("creates an mcp connector from setup", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "mcp-1",
          name: "mcp-gitlab-main",
          workspace_id: "default",
          is_active: true,
          visible_to_external_mcp: true,
          connection: {
            provider: "gitlab",
            transport: "streamable-http",
            url: "https://mcp.example.com/mcp",
            verify_tls: false,
            headers: { "Enabled-Groups": "core" },
            secret_headers: null,
            enabled_groups: "core",
            allowed_tool_names: ["search_issues", "create_issue", "comment_issue"],
            timeout_seconds: 30,
            tool_name_prefix: "mcp_gitlab_main",
            llm_connector_name: "example-llm",
            usage_description: "Use for GitLab MCP tools",
            extra_settings: {},
          },
          has_secret_headers: true,
          secret_header_names: ["Authorization"],
          tool_count: 0,
          last_sync_status: "pending",
          last_sync_error: null,
          last_sync_at: null,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click((await screen.findAllByRole("button", { name: "Добавить подключение" }))[4]!);

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "mcp-gitlab-main");
    await user.selectOptions(within(dialog).getByLabelText("Провайдер"), "gitlab");
    await user.type(within(dialog).getByLabelText("URL MCP"), "https://mcp.example.com/mcp");
    await user.type(within(dialog).getByLabelText("Префикс инструментов"), "mcp_gitlab_main");
    await user.type(within(dialog).getByLabelText("Включённые группы"), "core");
    await user.type(
      within(dialog).getByLabelText("Разрешённые методы MCP"),
      "search_issues, create_issue\ncomment_issue"
    );
    await user.type(within(dialog).getByLabelText("Подключение LLM"), "example-llm");
    await user.type(within(dialog).getByLabelText("Когда использовать"), "Use for GitLab MCP tools");
    await user.click(within(dialog).getByLabelText("Публиковать через внешний MCP"));
    fireEvent.change(within(dialog).getByLabelText("JSON публичных заголовков"), {
      target: { value: '{"Enabled-Groups":"core"}' },
    });
    fireEvent.change(
      within(dialog).getByLabelText("JSON секретных заголовков"),
      {
        target: { value: '[{"name":"Authorization","value":"token","mode":"bearer"}]' },
      }
    );
    await user.click(within(dialog).getByLabelText("Проверять TLS"));
    await user.click(within(dialog).getByRole("button", { name: "Создать подключение" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/mcp-connectors",
        expect.objectContaining({
          method: "POST",
          cache: "no-store",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      name: "mcp-gitlab-main",
      workspace_id: "default",
      is_active: true,
      visible_to_external_mcp: true,
      connection: {
        provider: "gitlab",
        transport: "streamable-http",
        url: "https://mcp.example.com/mcp",
        verify_tls: false,
        headers: { "Enabled-Groups": "core" },
        secret_headers: [{ name: "Authorization", value: "token", mode: "bearer" }],
        enabled_groups: "core",
        allowed_tool_names: ["search_issues", "create_issue", "comment_issue"],
        timeout_seconds: 30,
        tool_name_prefix: "mcp_gitlab_main",
        llm_connector_name: "example-llm",
        usage_description: "Use for GitLab MCP tools",
        extra_settings: {},
      },
    });

    await waitFor(() => {
      expect(within(screen.getByRole("main")).getByText("mcp-gitlab-main")).toBeInTheDocument();
    });
  });

  it("preserves mcp secret headers when edit field is blank", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "mcp-1",
          name: "mcp-gitlab-main",
          workspace_id: "default",
          is_active: true,
          visible_to_external_mcp: false,
          connection: {
            provider: "gitlab",
            transport: "streamable-http",
            url: "https://mcp.example.com/mcp",
            verify_tls: true,
            headers: {},
            secret_headers: null,
            enabled_groups: "core",
            allowed_tool_names: [],
            timeout_seconds: 30,
            tool_name_prefix: "mcp_gitlab_main",
            llm_connector_name: "example-llm",
            usage_description: "Use for GitLab MCP tools",
            extra_settings: {},
          },
          has_secret_headers: true,
          secret_header_names: ["Authorization"],
          tool_count: 0,
          last_sync_status: "pending",
          last_sync_error: null,
          last_sync_at: null,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage({
      mcpConnectors: [
        {
          id: "mcp-1",
          name: "mcp-gitlab-main",
          workspace_id: "default",
          is_active: true,
          visible_to_external_mcp: true,
          connection: {
            provider: "gitlab",
            transport: "streamable-http",
            url: "https://mcp.example.com/mcp",
            verify_tls: true,
            headers: {},
            secret_headers: null,
            enabled_groups: "core",
            allowed_tool_names: [],
            timeout_seconds: 30,
            tool_name_prefix: "mcp_gitlab_main",
            llm_connector_name: "example-llm",
            usage_description: "Use for GitLab MCP tools",
            extra_settings: {},
          },
          has_secret_headers: true,
          secret_header_names: ["Authorization"],
          tool_count: 0,
          last_sync_status: "pending",
          last_sync_error: null,
          last_sync_at: null,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        },
      ],
    });

    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Публиковать через внешний MCP")).toBeChecked();
    await user.click(within(dialog).getByLabelText("Публиковать через внешний MCP"));
    await user.click(within(dialog).getByRole("button", { name: "Сохранить подключение" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/mcp-connectors/mcp-1?workspace_id=default",
        expect.objectContaining({
          method: "PUT",
          cache: "no-store",
        })
      );
    });
    const requestBody = JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body));
    expect(requestBody.connection.secret_headers).toBeNull();
    expect(requestBody.visible_to_external_mcp).toBe(false);
  });

  it("syncs mcp tools from the connector card", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ synced: true, tool_count: 2, status: "success" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify([
            {
              id: "mcp-1",
              name: "mcp-gitlab-main",
              workspace_id: "default",
              is_active: true,
              visible_to_external_mcp: true,
              connection: {
                provider: "gitlab",
                transport: "streamable-http",
                url: "https://mcp.example.com/mcp",
                verify_tls: true,
                headers: {},
                secret_headers: null,
                enabled_groups: "core",
                allowed_tool_names: [],
                timeout_seconds: 30,
                tool_name_prefix: "mcp_gitlab_main",
                llm_connector_name: "example-llm",
                usage_description: "Use for GitLab MCP tools",
                extra_settings: {},
              },
              has_secret_headers: true,
              secret_header_names: ["Authorization"],
              tool_count: 2,
              last_sync_status: "success",
              last_sync_error: null,
              last_sync_at: "2026-04-05T13:00:00Z",
              created_at: "2026-04-05T13:00:00Z",
              updated_at: "2026-04-05T13:00:00Z",
            },
          ]),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage({
      mcpConnectors: [
        {
          id: "mcp-1",
          name: "mcp-gitlab-main",
          workspace_id: "default",
          is_active: true,
          visible_to_external_mcp: true,
          connection: {
            provider: "gitlab",
            transport: "streamable-http",
            url: "https://mcp.example.com/mcp",
            verify_tls: true,
            headers: {},
            secret_headers: null,
            enabled_groups: "core",
            allowed_tool_names: [],
            timeout_seconds: 30,
            tool_name_prefix: "mcp_gitlab_main",
            llm_connector_name: "example-llm",
            usage_description: "Use for GitLab MCP tools",
            extra_settings: {},
          },
          has_secret_headers: true,
          secret_header_names: ["Authorization"],
          tool_count: 0,
          last_sync_status: "pending",
          last_sync_error: null,
          last_sync_at: null,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        },
      ],
    });

    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click(await screen.findByRole("button", { name: "Синхронизировать" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/mcp-connectors/mcp-1/sync?workspace_id=default",
        expect.objectContaining({ method: "POST", cache: "no-store" })
      );
    });
    await user.click(screen.getByRole("button", { name: "Просмотр: mcp-gitlab-main" }));
    await waitFor(() => {
      expect(screen.getByText("Инструменты")).toBeInTheDocument();
      expect(screen.getByText("2")).toBeInTheDocument();
    });
  });

  it("creates an airflow connector from setup", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "airflow-1",
          name: "airflow-main",
          workspace_id: "default",
          is_active: true,
          connection: {
            provider: "airflow",
            base_url: "https://airflow.example.com",
            username: "airflow-user",
            password: null,
            use_when: "Use for read-only Airflow DAG inspection",
            auth_type: "basic",
            verify_tls: true,
            api_path: "/api/v1",
            dag_limit: 8,
            dag_run_limit: 7,
            task_limit: 6,
            log_max_chars: 5000,
            timeout_seconds: 5,
            extra_settings: {},
          },
          has_password: true,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click((await screen.findAllByRole("button", { name: "Добавить Airflow" }))[0]!);

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "airflow-main");
    await user.clear(within(dialog).getByLabelText("Базовый URL"));
    await user.type(within(dialog).getByLabelText("Базовый URL"), "https://airflow.example.com/");
    await user.type(within(dialog).getByLabelText("Имя пользователя"), "airflow-user");
    await user.type(within(dialog).getByLabelText("Пароль"), "secret-password");
    await user.clear(within(dialog).getByLabelText("Когда использовать"));
    await user.type(
      within(dialog).getByLabelText("Когда использовать"),
      "Use for read-only Airflow DAG inspection"
    );
    await user.clear(within(dialog).getByLabelText("Лимит DAG"));
    await user.type(within(dialog).getByLabelText("Лимит DAG"), "8");
    await user.clear(within(dialog).getByLabelText("Лимит запусков DAG"));
    await user.type(within(dialog).getByLabelText("Лимит запусков DAG"), "7");
    await user.clear(within(dialog).getByLabelText("Лимит задач"));
    await user.type(within(dialog).getByLabelText("Лимит задач"), "6");
    await user.clear(within(dialog).getByLabelText("Максимум символов в логе"));
    await user.type(within(dialog).getByLabelText("Максимум символов в логе"), "5000");
    await user.clear(within(dialog).getByLabelText("Таймаут, секунды"));
    await user.type(within(dialog).getByLabelText("Таймаут, секунды"), "5");
    await user.click(within(dialog).getByRole("button", { name: "Создать подключение" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/airflow-connectors",
        expect.objectContaining({
          method: "POST",
          cache: "no-store",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      name: "airflow-main",
      workspace_id: "default",
      is_active: true,
      connection: {
        provider: "airflow",
        base_url: "https://airflow.example.com/",
        username: "airflow-user",
        password: "secret-password",
        use_when: "Use for read-only Airflow DAG inspection",
        auth_type: "basic",
        verify_tls: true,
        api_path: "/api/v1",
        dag_limit: 8,
        dag_run_limit: 7,
        task_limit: 6,
        log_max_chars: 5000,
        timeout_seconds: 5,
        extra_settings: {},
      },
    });

    await waitFor(() => {
      expect(within(screen.getByRole("main")).getByText("airflow-main")).toBeInTheDocument();
    });
  });

  it("creates a kubernetes connector from setup", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "k8s-1",
          name: "k8s-prod",
          workspace_id: "default",
          is_active: true,
          visible_to_default_assistant: true,
          has_bearer_token: true,
          connection: {
            provider: "kubernetes",
            base_url: "https://k8s.example.com",
            bearer_token: null,
            use_when: "Use for read-only prod Kubernetes diagnostics",
            auth_type: "bearer",
            verify_tls: true,
            namespace_allowlist: ["prod", "analytics"],
            default_limit: 15,
            max_limit: 40,
            log_max_chars: 7000,
            timeout_seconds: 9,
            extra_settings: {},
          },
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click((await screen.findAllByRole("button", { name: "Добавить K8s" }))[0]!);

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "k8s-prod");
    await user.type(within(dialog).getByLabelText("URL API-сервера"), "https://k8s.example.com/");
    await user.type(within(dialog).getByLabelText("Bearer-токен"), "secret-token");
    await user.clear(within(dialog).getByLabelText("Когда использовать"));
    await user.type(
      within(dialog).getByLabelText("Когда использовать"),
      "Use for read-only prod Kubernetes diagnostics"
    );
    await user.type(within(dialog).getByLabelText("Разрешённые пространства имён"), "prod, analytics");
    await user.clear(within(dialog).getByLabelText("Лимит по умолчанию"));
    await user.type(within(dialog).getByLabelText("Лимит по умолчанию"), "15");
    await user.clear(within(dialog).getByLabelText("Максимальный лимит"));
    await user.type(within(dialog).getByLabelText("Максимальный лимит"), "40");
    await user.clear(within(dialog).getByLabelText("Максимум символов в логе"));
    await user.type(within(dialog).getByLabelText("Максимум символов в логе"), "7000");
    await user.clear(within(dialog).getByLabelText("Таймаут, секунды"));
    await user.type(within(dialog).getByLabelText("Таймаут, секунды"), "9");
    await user.click(within(dialog).getByLabelText("Виден ассистенту по умолчанию"));
    await user.click(within(dialog).getByRole("button", { name: "Создать подключение" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/kubernetes-connectors",
        expect.objectContaining({
          method: "POST",
          cache: "no-store",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      name: "k8s-prod",
      workspace_id: "default",
      is_active: true,
      visible_to_default_assistant: true,
      connection: {
        provider: "kubernetes",
        base_url: "https://k8s.example.com/",
        bearer_token: "secret-token",
        use_when: "Use for read-only prod Kubernetes diagnostics",
        auth_type: "bearer",
        verify_tls: true,
        namespace_allowlist: ["prod", "analytics"],
        default_limit: 15,
        max_limit: 40,
        log_max_chars: 7000,
        timeout_seconds: 9,
        extra_settings: {},
      },
    });

    await waitFor(() => {
      expect(within(screen.getByRole("main")).getByText("k8s-prod")).toBeInTheDocument();
    });
  });

  it("creates a victoriametrics connector from setup", async () => {
    const user = userEvent.setup();
    const template = {
      template_id: "pod_restarts",
      title: "Pod restarts",
      use_when: "Inspect pod restart count",
      query_template: 'kube_pod_container_status_restarts_total{namespace="$namespace"}',
      mode: "both",
      allowed_filters: ["namespace"],
      default_step: "60s",
      max_range_seconds: 86400,
      unit: "count",
      result_limit: 10,
    };
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "vm-1",
          name: "vm-prod",
          workspace_id: "default",
          is_active: true,
          visible_to_default_assistant: true,
          has_bearer_token: true,
          connection: {
            provider: "victoriametrics",
            base_url: "https://vm.example.com",
            bearer_token: null,
            use_when: "Use for metric diagnostics",
            auth_type: "bearer",
            verify_tls: true,
            query_path: "/prometheus/api/v1/query",
            query_range_path: "/prometheus/api/v1/query_range",
            templates: [template],
            timeout_seconds: 9,
            max_series: 15,
            max_datapoints: 500,
            extra_settings: {},
          },
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click((await screen.findAllByRole("button", { name: "Добавить VM" }))[0]!);

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "vm-prod");
    await user.type(within(dialog).getByLabelText("Базовый URL"), "https://vm.example.com/");
    await user.selectOptions(within(dialog).getByLabelText("Тип аутентификации"), "bearer");
    await user.type(within(dialog).getByLabelText("Bearer-токен"), "vm-token");
    await user.clear(within(dialog).getByLabelText("Когда использовать"));
    await user.type(within(dialog).getByLabelText("Когда использовать"), "Use for metric diagnostics");
    fireEvent.change(within(dialog).getByLabelText("JSON шаблонов"), {
      target: { value: JSON.stringify([template]) },
    });
    await user.clear(within(dialog).getByLabelText("Максимум серий"));
    await user.type(within(dialog).getByLabelText("Максимум серий"), "15");
    await user.clear(within(dialog).getByLabelText("Максимум точек данных"));
    await user.type(within(dialog).getByLabelText("Максимум точек данных"), "500");
    await user.clear(within(dialog).getByLabelText("Таймаут, секунды"));
    await user.type(within(dialog).getByLabelText("Таймаут, секунды"), "9");
    await user.click(within(dialog).getByLabelText("Виден ассистенту по умолчанию"));
    await user.click(within(dialog).getByRole("button", { name: "Создать подключение" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/victoriametrics-connectors",
        expect.objectContaining({
          method: "POST",
          cache: "no-store",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      name: "vm-prod",
      workspace_id: "default",
      is_active: true,
      visible_to_default_assistant: true,
      connection: {
        provider: "victoriametrics",
        base_url: "https://vm.example.com/",
        bearer_token: "vm-token",
        use_when: "Use for metric diagnostics",
        auth_type: "bearer",
        verify_tls: true,
        query_path: "/prometheus/api/v1/query",
        query_range_path: "/prometheus/api/v1/query_range",
        templates: [template],
        timeout_seconds: 9,
        max_series: 15,
        max_datapoints: 500,
        extra_settings: {},
      },
    });

    await waitFor(() => {
      expect(within(screen.getByRole("main")).getByText("vm-prod")).toBeInTheDocument();
    });
  });

  it("creates a documentation connector with use_when and default visibility", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "docs-1",
          name: "platform-docs",
          workspace_id: "default",
          is_active: true,
          visible_to_default_assistant: false,
          connection: {
            provider: "docsy_hugo",
            base_url: "https://docs.company.com",
            index_url: "https://docs.company.com/llm-docs-index.json",
            content_format: "markdown",
            username: null,
            usage_description: "Use for dbt onboarding",
            auth_type: "none",
            verify_tls: true,
            include_paths: ["/docs/dbt"],
            exclude_paths: [],
            sync_enabled: true,
            sync_interval_minutes: 60,
            qdrant_base_url: "http://qdrant:6333",
            qdrant_collection_name: "documentation_chunks",
            qdrant_prefer_grpc: false,
            vector_size: 1024,
            embedding_base_url: null,
            embedding_model: "BAAI/bge-small-en-v1.5",
            embedding_endpoint_path: "/v1/embeddings",
            embedding_timeout_seconds: 120,
            extra_settings: {},
          },
          has_api_token: false,
          has_embedding_api_key: false,
          has_qdrant_api_key: false,
          last_sync_status: null,
          last_sync_at: null,
          next_sync_at: null,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click((await screen.findAllByRole("button", { name: "Добавить документацию" }))[0]!);

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "platform-docs");
    await user.type(within(dialog).getByLabelText("Базовый URL"), "https://docs.company.com");
    await user.type(
      within(dialog).getByLabelText("URL индекса"),
      "https://docs.company.com/llm-docs-index.json"
    );
    await user.type(within(dialog).getByLabelText("Применять, когда"), "Use for dbt onboarding");
    await user.type(within(dialog).getByLabelText("Включённые пути"), "/docs/dbt");
    await user.click(within(dialog).getByLabelText("Ассистент по умолчанию"));
    await user.click(within(dialog).getByRole("button", { name: "Создать подключение" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/documentation-connectors",
        expect.objectContaining({ method: "POST", cache: "no-store" })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toMatchObject({
      name: "platform-docs",
      workspace_id: "default",
      is_active: true,
      visible_to_default_assistant: false,
      connection: {
        usage_description: "Use for dbt onboarding",
        include_paths: ["/docs/dbt"],
      },
    });
  });

  it("creates an llm connector with a context window override", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "llm-1",
          name: "example-llm",
          workspace_id: "default",
          is_active: true,
          is_default: true,
          connection: {
            base_url: "https://llmlite.example/v1",
            model: "ExampleReasoningModel",
            api_keys: null,
            retained_api_key_indexes: null,
            endpoint_path: "/chat/completions",
            verify_tls: false,
            temperature: 0.1,
            max_tokens: 12000,
            max_tokens_parameter: "max_completion_tokens",
            headers: {},
            extra_settings: {
              context_window_tokens: 190000,
              require_explicit_final_answer: true,
            },
          },
          has_api_key: false,
          api_key_count: 0,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click((await screen.findAllByRole("button", { name: "Добавить LLM" }))[0]!);

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "example-llm");
    await user.type(within(dialog).getByLabelText("Базовый URL"), "https://llmlite.example/v1");
    await user.type(within(dialog).getByLabelText("Модель"), "ExampleReasoningModel");
    await user.clear(within(dialog).getByLabelText("Максимум токенов ответа"));
    await user.type(within(dialog).getByLabelText("Максимум токенов ответа"), "12000");
    await user.selectOptions(
      within(dialog).getByLabelText("Параметр запроса максимума токенов"),
      "max_completion_tokens"
    );
    await user.type(within(dialog).getByLabelText("Токены контекстного окна"), "190000");
    await user.selectOptions(within(dialog).getByLabelText("Уровень reasoning"), "high");
    await user.click(within(dialog).getByLabelText("Требовать инструмент финального ответа"));
    await user.click(within(dialog).getByLabelText("Проверять TLS"));
    await user.click(within(dialog).getByLabelText("По умолчанию для стандартного ассистента"));
    await user.click(within(dialog).getByRole("button", { name: "Создать подключение" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/llm-connectors",
        expect.objectContaining({
          method: "POST",
          cache: "no-store",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      name: "example-llm",
      workspace_id: "default",
      is_active: true,
      is_default: true,
      connection: {
        base_url: "https://llmlite.example/v1",
        model: "ExampleReasoningModel",
        api_key: null,
        api_keys: null,
        retained_api_key_indexes: null,
        endpoint_path: "/chat/completions",
        verify_tls: false,
        temperature: 0.1,
        top_p: null,
        reasoning_effort: "high",
        max_tokens: 12000,
        max_tokens_parameter: "max_completion_tokens",
        headers: {},
        extra_settings: {
          context_window_tokens: 190000,
          require_explicit_final_answer: true,
        },
      },
    });

    await waitFor(() => {
      expect(within(screen.getByRole("main")).getByText("example-llm")).toBeInTheDocument();
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Просмотр: example-llm" }));
    expect(within(screen.getByRole("dialog")).getByText("По умолчанию")).toBeInTheDocument();
  });

  it("creates an impala database connector from setup", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "impala-1",
          name: "impala-prod",
          workspace_id: "default",
          is_active: true,
          connection: {
            engine: "impala",
            host: "192.0.2.50",
            port: 21050,
            username: "balov_aa",
            password: null,
            usage_description: "Use for raw lake ad-hoc checks",
            database: null,
            catalog: null,
            schema: "default",
            secure: true,
            verify: false,
            extra_settings: {
              auth_type: "LDAP",
              retries: 1,
            },
          },
          has_password: true,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click(await screen.findByRole("button", { name: "Добавить базу данных" }));

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "impala-prod");
    await user.selectOptions(within(dialog).getByLabelText("Движок"), "impala");
    await user.type(within(dialog).getByLabelText("Хост"), "192.0.2.50");
    await user.clear(within(dialog).getByLabelText("Порт"));
    await user.type(within(dialog).getByLabelText("Порт"), "21050");
    await user.type(within(dialog).getByLabelText("Имя пользователя"), "balov_aa");
    await user.type(within(dialog).getByLabelText("Пароль"), "secret");
    await user.type(within(dialog).getByLabelText("Когда использовать"), "Use for raw lake ad-hoc checks");
    await user.type(within(dialog).getByLabelText("Схема"), "default");
    await user.click(within(dialog).getByLabelText("Использовать TLS"));
    await user.click(within(dialog).getByLabelText("Проверять сертификат TLS"));
    await user.click(within(dialog).getByRole("button", { name: "Создать подключение" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/connectors",
        expect.objectContaining({
          method: "POST",
          cache: "no-store",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      name: "impala-prod",
      workspace_id: "default",
      is_active: true,
      connection: {
        engine: "impala",
        host: "192.0.2.50",
        port: 21050,
        username: "balov_aa",
        password: "secret",
        usage_description: "Use for raw lake ad-hoc checks",
        database: null,
        catalog: null,
        schema: "default",
        secure: true,
        verify: false,
        extra_settings: {
          auth_type: "LDAP",
          retries: 1,
        },
        extra_credentials: null,
      },
    });

    await waitFor(() => {
      expect(within(screen.getByRole("main")).getByText("impala-prod")).toBeInTheDocument();
    });
  });

  it("creates a trino connector with extra credentials and arbitrary settings", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "trino-dg-1",
          name: "trino-dg",
          workspace_id: "default",
          is_active: true,
          has_password: true,
          has_extra_credentials: true,
          connection: {
            engine: "trino",
            host: "192.0.2.7",
            port: 443,
            username: "svc-trino",
            password: null,
            usage_description: null,
            database: null,
            catalog: "iceberg_dg",
            schema: "iceberg_dg",
            secure: true,
            verify: false,
            extra_settings: {
              set_query_max_run_time: false,
              http_headers: { Host: "trino.example.com" },
            },
            extra_credentials: { td_password: null, td_user: null },
          },
          created_at: "2026-08-27T13:00:00Z",
          updated_at: "2026-08-27T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();
    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click(await screen.findByRole("button", { name: "Добавить базу данных" }));

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "trino-dg");
    await user.selectOptions(within(dialog).getByLabelText("Движок"), "trino");
    await user.type(within(dialog).getByLabelText("Хост"), "192.0.2.7");
    await user.clear(within(dialog).getByLabelText("Порт"));
    await user.type(within(dialog).getByLabelText("Порт"), "443");
    await user.type(within(dialog).getByLabelText("Имя пользователя"), "svc-trino");
    await user.type(within(dialog).getByLabelText("Пароль"), "trino-secret");
    await user.type(within(dialog).getByLabelText("Каталог"), "iceberg_dg");
    await user.type(within(dialog).getByLabelText("Схема"), "iceberg_dg");
    await user.click(within(dialog).getByLabelText("Использовать TLS"));
    await user.click(within(dialog).getByLabelText("Проверять сертификат TLS"));
    fireEvent.change(within(dialog).getByLabelText("Дополнительные настройки (JSON)"), {
      target: {
        value: JSON.stringify({
          set_query_max_run_time: false,
          http_headers: { Host: "trino.example.com" },
        }),
      },
    });

    await user.click(within(dialog).getByRole("button", { name: "Добавить учётные данные" }));
    await user.type(within(dialog).getByPlaceholderText("td_user"), "td_user");
    await user.type(within(dialog).getByPlaceholderText("Значение учётных данных"), "svc-teradata");
    await user.click(within(dialog).getByRole("button", { name: "Добавить учётные данные" }));
    const names = within(dialog).getAllByPlaceholderText("td_user");
    const values = within(dialog).getAllByPlaceholderText("Значение учётных данных");
    await user.type(names[1]!, "td_password");
    await user.type(values[1]!, "teradata-secret");
    await user.click(within(dialog).getByRole("button", { name: "Создать подключение" }));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    const body = JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body));
    expect(body.connection.extra_credentials).toEqual({
      td_user: "svc-teradata",
      td_password: "teradata-secret",
    });
    expect(body.connection.extra_settings).toEqual({
      set_query_max_run_time: false,
      http_headers: { Host: "trino.example.com" },
    });
  });

  it("preserves stored trino credentials and unknown settings on edit", async () => {
    const user = userEvent.setup();
    const connector: DatabaseConnectorItem = {
      id: "trino-dg-1",
      name: "trino-dg",
      workspace_id: "default",
      is_active: true,
      has_password: true,
      has_extra_credentials: true,
      connection: {
        engine: "trino",
        host: "192.0.2.7",
        port: 443,
        username: "svc-trino",
        usage_description: null,
        database: null,
        catalog: "iceberg_dg",
        schema: "iceberg_dg",
        secure: true,
        verify: false,
        extra_settings: {
          custom_property: "keep-me",
          http_headers: { Host: "trino.example.com" },
        },
        extra_credentials: { td_password: null, td_user: null },
      },
      created_at: "2026-08-27T13:00:00Z",
      updated_at: "2026-08-27T13:00:00Z",
    };
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(connector), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage({ databaseConnectors: [connector] });
    await user.click(screen.getByRole("button", { name: /Настройки/ }));
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Дополнительные настройки (JSON)")).toHaveValue(
      JSON.stringify(connector.connection.extra_settings, null, 2)
    );
    expect(within(dialog).getAllByPlaceholderText("Оставьте пустым, чтобы не менять")).toHaveLength(2);
    await user.click(within(dialog).getByRole("button", { name: "Сохранить подключение" }));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    const body = JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body));
    expect(body.connection.extra_credentials).toEqual({
      td_password: null,
      td_user: null,
    });
    expect(body.connection.extra_settings).toEqual(connector.connection.extra_settings);
  });

  it("blocks oversized manual workspace skills before submit", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    const governanceTab = screen
      .getAllByRole("button", { name: /Управление/ })
      .find((button) => button.getAttribute("aria-pressed") !== null);
    expect(governanceTab).toBeDefined();
    await user.click(governanceTab!);
    await user.click((await screen.findAllByRole("button", { name: "Добавить навык" }))[0]!);

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "support-tone");
    await user.selectOptions(
      within(dialog).getByRole("combobox", { name: "Источник" }),
      "manual_markdown"
    );
    await user.type(within(dialog).getByLabelText("Применять, когда"), "Use for support replies");
    fireEvent.change(within(dialog).getByLabelText("Текст навыка вручную"), {
      target: { value: "a".repeat(32_768) },
    });
    await user.click(within(dialog).getByRole("button", { name: "Создать навык" }));

    expect(
      await within(dialog).findByText(
        "Текст навыка слишком велик после добавления подсказки о применении"
      )
    ).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("keeps an unsaved connector draft after closing and clears it after saving", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "clickhouse-draft-1",
          name: "clickhouse-draft",
          workspace_id: "default",
          is_active: true,
          connection: {
            engine: "clickhouse",
            host: "draft-host.example.com",
            port: 8123,
            username: "draft-user",
            password: null,
            usage_description: null,
            database: null,
            catalog: null,
            schema: null,
            secure: false,
            verify: true,
            extra_settings: {},
          },
          has_password: false,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Настройки/ }));

    await user.click(await screen.findByRole("button", { name: "Добавить базу данных" }));
    let dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "clickhouse-draft");
    await user.type(within(dialog).getByLabelText("Хост"), "draft-host.example.com");
    await user.type(within(dialog).getByLabelText("Имя пользователя"), "draft-user");
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Добавить базу данных" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Название")).toHaveValue("clickhouse-draft");
    expect(within(dialog).getByLabelText("Хост")).toHaveValue("draft-host.example.com");
    expect(within(dialog).getByLabelText("Имя пользователя")).toHaveValue("draft-user");

    await user.click(within(dialog).getByRole("button", { name: "Создать подключение" }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/connectors",
        expect.objectContaining({ method: "POST" })
      );
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(await screen.findByRole("button", { name: "Добавить базу данных" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Название")).toHaveValue("");
    expect(within(dialog).getByLabelText("Хост")).toHaveValue("");
    expect(within(dialog).getByLabelText("Имя пользователя")).toHaveValue("");
  });

  it("keeps an unsaved skill draft after closing and clears it after saving", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "skill-draft-1",
          workspace_id: "default",
          name: "draft-skill",
          capability_family: "chat_instruction",
          engine_scope: "any",
          priority: 100,
          source_kind: "manual_markdown",
          source_url: null,
          status: "active",
          validation_status: "valid",
          visible_to_default_assistant: true,
          title: null,
          summary: null,
          content_markdown: "---\nuse_when: \"support tone\"\n---\n\n# Draft",
          content_bundle: { assets: [] },
          content_sha256: null,
          last_validated_at: null,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();
    const governanceTab = screen
      .getAllByRole("button", { name: /Управление/ })
      .find((button) => button.getAttribute("aria-pressed") !== null);
    await user.click(governanceTab!);

    await user.click((await screen.findAllByRole("button", { name: "Добавить навык" }))[0]!);
    let dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Название"), "draft-skill");
    await user.selectOptions(within(dialog).getByRole("combobox", { name: "Источник" }), "manual_markdown");
    await user.type(within(dialog).getByLabelText("Применять, когда"), "support tone");
    await user.type(within(dialog).getByLabelText("Текст навыка вручную"), "# Draft");
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click((await screen.findAllByRole("button", { name: "Добавить навык" }))[0]!);
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Название")).toHaveValue("draft-skill");
    expect(within(dialog).getByRole("combobox", { name: "Источник" })).toHaveValue("manual_markdown");
    expect(within(dialog).getByLabelText("Применять, когда")).toHaveValue("support tone");
    expect(within(dialog).getByLabelText("Текст навыка вручную")).toHaveValue("# Draft");

    await user.click(within(dialog).getByRole("button", { name: "Создать навык" }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith("/api/admin/skills", expect.objectContaining({ method: "POST" }));
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click((await screen.findAllByRole("button", { name: "Добавить навык" }))[0]!);
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Название")).toHaveValue("");
    expect(within(dialog).getByRole("combobox", { name: "Источник" })).toHaveValue("github_markdown_url");
  });

  it("keeps an unsaved agent draft after closing and clears it after saving", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "agent-draft-1",
          workspace_id: "default",
          slug: "draft-agent",
          title: "Draft Agent",
          description: "Agent with an unfinished draft",
          admin_prompt: null,
          is_active: true,
          llm_connector_name: null,
          allowed_database_connector_names: [],
          allowed_rag_connector_names: [],
          allowed_documentation_connector_names: [],
          allowed_gitlab_connector_names: [],
          allowed_mcp_connector_names: [],
          allowed_airflow_connector_names: [],
          allowed_kubernetes_connector_names: [],
          allowed_victoriametrics_connector_names: [],
          allowed_workspace_skill_ids: [],
          settings: { run_sql_allowed: true, temperature: null, max_tokens: null },
          star_count: 0,
          is_starred: false,
          is_favorite: false,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();
    const governanceTab = screen
      .getAllByRole("button", { name: /Управление/ })
      .find((button) => button.getAttribute("aria-pressed") !== null);
    await user.click(governanceTab!);

    await user.click((await screen.findAllByRole("button", { name: "Добавить агента" }))[0]!);
    let dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Идентификатор"), "draft-agent");
    await user.type(within(dialog).getByLabelText("Название"), "Draft Agent");
    await user.type(within(dialog).getByLabelText("Описание"), "Agent with an unfinished draft");
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click((await screen.findAllByRole("button", { name: "Добавить агента" }))[0]!);
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Идентификатор")).toHaveValue("draft-agent");
    expect(within(dialog).getByLabelText("Название")).toHaveValue("Draft Agent");
    expect(within(dialog).getByLabelText("Описание")).toHaveValue("Agent with an unfinished draft");

    await user.click(within(dialog).getByRole("button", { name: "Создать агента" }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith("/api/admin/agents", expect.objectContaining({ method: "POST" }));
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click((await screen.findAllByRole("button", { name: "Добавить агента" }))[0]!);
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Идентификатор")).toHaveValue("");
    expect(within(dialog).getByLabelText("Название")).toHaveValue("");
  });
});


it.each([false, true])("round-trips all LLM settings with store=%s without losing nulls, headers, extra options or keys", async (store) => {
  const user = userEvent.setup();
  const item: LlmConnectorItem = {
    id: "llm-round-trip", name: "Configured LLM", workspace_id: "default", is_active: true,
    is_default: true, has_api_key: true, api_key_count: 2,
    connection: {
      base_url: "https://llm.example/v1", model: "test-model", endpoint_path: "/responses",
      verify_tls: true, temperature: null, top_p: 0.95, max_tokens: null,
      reasoning_effort: "medium",
      max_tokens_parameter: "max_completion_tokens", headers: { "X-Client": "analytics" },
      extra_settings: { chat_template_enable_thinking: false, llm_retry_attempts: 2,
        responses_endpoint_path: "/responses", context_window_tokens: 180000,
        require_explicit_final_answer: false, verify_tls: true, store,
        native_compaction_enabled: true, native_compaction_threshold_tokens: 1000 },
    },
    created_at: "2026-09-07T12:00:00Z", updated_at: "2026-09-07T12:00:00Z",
  };
  const fetchSpy = vi.fn().mockResolvedValue(new Response(JSON.stringify(item), {status: 200}));
  vi.stubGlobal("fetch", fetchSpy);
  renderAdminPage({ llmConnectors: [item] });
  await user.click(screen.getByRole("button", { name: /Настройки/ }));
  await user.click(await screen.findByRole("button", { name: "Редактировать" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText("Температура")).toHaveValue(null);
  expect(within(dialog).getByLabelText("Максимум токенов ответа")).toHaveValue(null);
  expect(within(dialog).getByLabelText("Top P")).toHaveValue(0.95);
  expect(within(dialog).getByLabelText("Хранение ответов у провайдера (store)")).toHaveValue(String(store));
  expect(within(dialog).getByLabelText("Нативное сжатие контекста")).toBeChecked();
  expect(within(dialog).getByLabelText("Порог нативного сжатия (токены)")).toHaveValue(1000);
  expect(within(dialog).getByLabelText("Уровень reasoning")).toHaveValue("medium");
  const headers = within(dialog).getByLabelText("JSON HTTP-заголовков");
  expect(JSON.parse((headers as HTMLTextAreaElement).value)).toEqual(item.connection.headers);
  const extra = within(dialog).getByLabelText("JSON дополнительных настроек LLM");
  expect(JSON.parse((extra as HTMLTextAreaElement).value)).toEqual({
    chat_template_enable_thinking: false, llm_retry_attempts: 2, responses_endpoint_path: "/responses",
  });
  expect(within(dialog).getByText("Сохранённый ключ #1")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Сохранить подключение" }));
  await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
  const body = JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body));
  expect(body.connection).toMatchObject({
    temperature: null, top_p: 0.95, reasoning_effort: "medium", max_tokens: null, headers: item.connection.headers,
    retained_api_key_indexes: [0, 1], api_keys: null,
    extra_settings: { chat_template_enable_thinking: false, llm_retry_attempts: 2,
      responses_endpoint_path: "/responses", context_window_tokens: 180000, require_explicit_final_answer: false,
      native_compaction_enabled: true, native_compaction_threshold_tokens: 1000, store },
  });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await user.click(await screen.findByRole("button", { name: "Редактировать" }));
  const reopened = await screen.findByRole("dialog");
  expect(within(reopened).getByLabelText("Хранение ответов у провайдера (store)")).toHaveValue(String(store));
  await user.selectOptions(within(reopened).getByLabelText("Хранение ответов у провайдера (store)"), "");
  await user.clear(within(reopened).getByLabelText("Top P"));
  await user.selectOptions(within(reopened).getByLabelText("Уровень reasoning"), "");
  await user.click(within(reopened).getByRole("button", { name: "Сохранить подключение" }));
  await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(fetchSpy.mock.calls[1]?.[1]?.body)).connection.top_p).toBeNull();
  expect(JSON.parse(String(fetchSpy.mock.calls[1]?.[1]?.body)).connection.extra_settings).not.toHaveProperty("store");
  expect(JSON.parse(String(fetchSpy.mock.calls[1]?.[1]?.body)).connection.reasoning_effort).toBeNull();

});
