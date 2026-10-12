"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Badge,
  Button,
  Dialog,
  Input,
  Select,
  Switch,
  useToast,
} from "@rhetorica/ui";
import { Check, Copy, KeyRound, LoaderCircle, Plus, RefreshCw, Trash2 } from "lucide-react";

type ApiTokenScope = "api:read" | "api:write" | "mcp:read" | "mcp:execute";

export type ApiTokenItem = {
  id: string;
  name: string;
  token_hint: string;
  scopes: ApiTokenScope[];
  lifetime_days: number;
  auto_renew: boolean;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
  is_active: boolean;
};

export type CreatedApiToken = ApiTokenItem & {
  token: string;
  token_type: "bearer";
};

type ApiTokenDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

const scopeOptions: Array<{ value: ApiTokenScope; label: string; description: string }> = [
  { value: "mcp:read", label: "Обзор MCP", description: "Просмотр инструментов, ресурсов и истории" },
  { value: "mcp:execute", label: "Выполнение MCP", description: "Запуск управляемых SQL и аналитических инструментов" },
  { value: "api:read", label: "Чтение API", description: "Просмотр ресурсов API Rhetorica" },
  { value: "api:write", label: "Запись в API", description: "Изменение ресурсов API Rhetorica" },
];

export function toApiTokenMetadata(token: CreatedApiToken): ApiTokenItem {
  return {
    id: token.id,
    name: token.name,
    token_hint: token.token_hint,
    scopes: token.scopes,
    lifetime_days: token.lifetime_days,
    auto_renew: token.auto_renew,
    expires_at: token.expires_at,
    last_used_at: token.last_used_at,
    revoked_at: token.revoked_at,
    created_at: token.created_at,
    is_active: token.is_active,
  };
}

export function updateTokenScopes(
  current: ApiTokenScope[],
  scope: ApiTokenScope,
  checked: boolean,
): ApiTokenScope[] {
  const next = new Set(current);
  if (checked) {
    next.add(scope);
    if (scope === "mcp:execute") {
      next.add("mcp:read");
    }
  } else {
    next.delete(scope);
    if (scope === "mcp:read") {
      next.delete("mcp:execute");
    }
  }
  return scopeOptions.map((option) => option.value).filter((value) => next.has(value));
}

function formatDate(value: string | null): string {
  if (!value) {
    return "Никогда";
  }
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : value;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? `Запрос завершился с ошибкой (${response.status})`;
  } catch {
    return `Запрос завершился с ошибкой (${response.status})`;
  }
}

export function ApiTokenDialog({ open, onOpenChange }: ApiTokenDialogProps) {
  const { addToast } = useToast();
  const [tokens, setTokens] = useState<ApiTokenItem[]>([]);
  const [mcpUrl, setMcpUrl] = useState("http://localhost:8000/mcp");
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [name, setName] = useState("Мой MCP-клиент");
  const [lifetimeDays, setLifetimeDays] = useState("90");
  const [autoRenew, setAutoRenew] = useState(false);
  const [scopes, setScopes] = useState<ApiTokenScope[]>(["mcp:read", "mcp:execute"]);
  const [createdToken, setCreatedToken] = useState<CreatedApiToken | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [tokensResponse, configResponse] = await Promise.all([
        fetch("/api/auth/tokens", { cache: "no-store" }),
        fetch("/api/auth/tokens/config", { cache: "no-store" }),
      ]);
      if (!tokensResponse.ok) {
        throw new Error(await errorDetail(tokensResponse));
      }
      setTokens((await tokensResponse.json()) as ApiTokenItem[]);
      if (configResponse.ok) {
        const config = (await configResponse.json()) as { mcp_url?: string };
        if (config.mcp_url) {
          setMcpUrl(config.mcp_url);
        }
      }
    } catch (error) {
      addToast({
        type: "error",
        title: "Токены недоступны",
        description: error instanceof Error ? error.message : "Не удалось загрузить API токены",
      });
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    if (open) {
      void load();
      return;
    }
    setCreatedToken(null);
    setCopied(false);
  }, [load, open]);

  const snippets = useMemo(() => {
    if (!createdToken) {
      return null;
    }
    return {
      openai: JSON.stringify(
        {
          type: "mcp",
          server_label: "rhetorica",
          server_url: mcpUrl,
          authorization: createdToken.token,
          allowed_tools: [
            "list_connectors",
            "list_conversations",
            "get_conversation",
            "ask_chat",
            "run_sql",
          ],
          require_approval: "always",
        },
        null,
        2,
      ),
      anthropic: JSON.stringify(
        {
          type: "url",
          url: mcpUrl,
          name: "rhetorica",
          authorization_token: createdToken.token,
        },
        null,
        2,
      ),
    };
  }, [createdToken, mcpUrl]);

  const createToken = async (event: FormEvent) => {
    event.preventDefault();
    if (scopes.length === 0) {
      addToast({ type: "error", title: "Выберите область доступа", description: "Выберите хотя бы одно разрешение" });
      return;
    }
    setCreating(true);
    try {
      const response = await fetch("/api/auth/tokens", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          scopes,
          lifetime_days: Number(lifetimeDays),
          auto_renew: autoRenew,
        }),
      });
      if (!response.ok) {
        throw new Error(await errorDetail(response));
      }
      const token = (await response.json()) as CreatedApiToken;
      setCreatedToken(token);
      setTokens((current) => [toApiTokenMetadata(token), ...current]);
      setCopied(false);
    } catch (error) {
      addToast({
        type: "error",
        title: "Не удалось создать токен",
        description: error instanceof Error ? error.message : "Не удалось создать токен",
      });
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (tokenId: string) => {
    setRevokingId(tokenId);
    try {
      const response = await fetch(`/api/auth/tokens/${encodeURIComponent(tokenId)}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await errorDetail(response));
      }
      setTokens((current) =>
        current.map((token) =>
          token.id === tokenId
            ? { ...token, revoked_at: new Date().toISOString(), is_active: false }
            : token,
        ),
      );
    } catch (error) {
      addToast({
        type: "error",
        title: "Не удалось отозвать токен",
        description: error instanceof Error ? error.message : "Не удалось отозвать токен",
      });
    } finally {
      setRevokingId(null);
    }
  };

  const copy = async (value: string) => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    addToast({ type: "success", title: "Скопировано", description: "Токен скопирован в буфер обмена" });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="API & MCP токены"
      description="Выдавайте ограниченные по правам и отзываемые токены. Секрет показывается только один раз"
      className="max-h-[90vh] max-w-4xl overflow-y-auto"
    >
      <div className="grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <form onSubmit={createToken} className="space-y-4 rounded-2xl border border-border-strong bg-surface/80 p-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-primary">
            <Plus className="h-4 w-4 text-accent" />
            Новый токен
          </div>
          <Input
            label="Название токена"
            value={name}
            onChange={(event) => setName(event.target.value)}
            minLength={1}
            maxLength={128}
            required
            data-autofocus
          />
          <Select
            label="Срок действия"
            value={lifetimeDays}
            onChange={(event) => setLifetimeDays(event.target.value)}
            options={[
              { value: "90", label: "90 дней" },
              { value: "180", label: "180 дней" },
              { value: "365", label: "365 дней" },
            ]}
          />
          <Switch
            label="Продлевать срок действия, пока токен активен"
            checked={autoRenew}
            onChange={(event) => setAutoRenew(event.target.checked)}
          />
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-secondary">Области доступа</legend>
            {scopeOptions.map((scope) => (
              <label
                key={scope.value}
                className="flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong bg-surface p-3"
              >
                <input
                  type="checkbox"
                  checked={scopes.includes(scope.value)}
                  onChange={(event) =>
                    setScopes((current) =>
                      updateTokenScopes(current, scope.value, event.target.checked),
                    )
                  }
                  className="mt-1 h-4 w-4 accent-accent"
                />
                <span>
                  <span className="block text-sm text-primary">{scope.label}</span>
                  <span className="block text-xs text-secondary">{scope.description}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <Button type="submit" variant="primary" disabled={creating} className="w-full justify-center gap-2">
            {creating ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
            Создать токен
          </Button>
        </form>

        <section className="space-y-4">
          {createdToken && snippets ? (
            <div className="rounded-2xl border border-success-border bg-cyan-subtle p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-success">
                <Check className="h-4 w-4" /> Токен готов — скопируйте его сейчас
              </div>
              <p className="mt-1 text-xs text-success">После закрытия этого окна восстановить токен не получится.</p>
              <div className="mt-3 flex gap-2">
                <Input aria-label="Новый API токен" readOnly value={createdToken.token} className="font-mono text-xs" />
                <Button type="button" variant="outline" onClick={() => void copy(createdToken.token)} aria-label="Скопировать новый API токен">
                  {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                </Button>
              </div>
              <div className="mt-4 space-y-3">
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-success">URL для MCP Inspector</p>
                  <code className="block overflow-x-auto rounded-xl bg-cyan-subtle p-3 text-xs text-success">{mcpUrl}</code>
                </div>
                <details>
                  <summary className="cursor-pointer text-sm text-success">Инструмент OpenAI Responses</summary>
                  <pre className="mt-2 overflow-x-auto rounded-xl bg-cyan-subtle p-3 text-xs text-success">{snippets.openai}</pre>
                </details>
                <details>
                  <summary className="cursor-pointer text-sm text-success">MCP-сервер Claude API</summary>
                  <pre className="mt-2 overflow-x-auto rounded-xl bg-cyan-subtle p-3 text-xs text-success">{snippets.anthropic}</pre>
                </details>
              </div>
            </div>
          ) : null}

          <div className="rounded-2xl border border-border-strong bg-surface/80 p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-primary">Ваши токены</p>
                <p className="text-xs text-secondary">Отзывайте всё, что больше не используете.</p>
              </div>
              <Button type="button" size="sm" variant="ghost" onClick={() => void load()} disabled={loading} aria-label="Обновить API токены">
                <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              </Button>
            </div>
            {loading && tokens.length === 0 ? (
              <p className="text-sm text-secondary">Загрузка токенов…</p>
            ) : tokens.length === 0 ? (
              <p className="rounded-xl border border-dashed border-border-strong p-4 text-sm text-secondary">Токенов пока нет</p>
            ) : (
              <div className="space-y-2">
                {tokens.map((token) => (
                  <article key={token.id} className="rounded-xl border border-border-strong bg-surface p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="truncate text-sm font-medium text-primary">{token.name}</p>
                          <Badge variant={token.is_active ? "success" : "default"}>{token.is_active ? "Активен" : "Отозван"}</Badge>
                          {token.auto_renew ? <Badge variant="primary">Автопродление</Badge> : null}
                        </div>
                        <p className="mt-1 font-mono text-xs text-secondary">••••{token.token_hint}</p>
                        <p className="mt-1 text-xs text-secondary">Истекает {formatDate(token.expires_at)} · Последнее использование {formatDate(token.last_used_at)}</p>
                        <div className="mt-2 flex flex-wrap gap-1">
                          {token.scopes.map((scope) => <Badge key={scope} variant="default">{scope}</Badge>)}
                        </div>
                      </div>
                      {token.is_active ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="danger"
                          disabled={revokingId === token.id}
                          onClick={() => void revoke(token.id)}
                          aria-label={`Отозвать ${token.name}`}
                        >
                          {revokingId === token.id ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                        </Button>
                      ) : null}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>
    </Dialog>
  );
}
