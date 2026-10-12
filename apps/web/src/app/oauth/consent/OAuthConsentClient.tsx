"use client";

import { useEffect, useState } from "react";

type ConsentDetails = {
  client: { id: string; name: string; uri: string | null };
  redirect_host: string | null;
  scopes: string[];
  resource: string;
  expires_at: string;
};

const scopeLabels: Record<string, string> = {
  "mcp:read": "Смотреть агентов, подключения и историю разговоров",
  "mcp:execute": "Отправлять сообщения агентам и запускать разрешённые инструменты",
};

export function OAuthConsentClient({
  transaction,
  onRedirect = (url) => window.location.assign(url),
}: {
  transaction: string;
  onRedirect?: (url: string) => void;
}) {
  const [details, setDetails] = useState<ConsentDetails | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<"approve" | "deny" | null>(null);

  useEffect(() => {
    let active = true;
    void fetch(`/api/oauth/consent/${encodeURIComponent(transaction)}`, { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("consent_unavailable");
        }
        return (await response.json()) as ConsentDetails;
      })
      .then((body) => {
        if (active) setDetails(body);
      })
      .catch(() => {
        if (active) setError("Запрос устарел или уже был использован");
      });
    return () => {
      active = false;
    };
  }, [transaction]);

  const decide = async (action: "approve" | "deny") => {
    setSubmitting(action);
    setError(null);
    try {
      const response = await fetch(
        `/api/oauth/consent/${encodeURIComponent(transaction)}/${action}`,
        { method: "POST" },
      );
      const body = (await response.json()) as { redirect_uri?: string; detail?: string };
      if (!response.ok || !body.redirect_uri) {
        throw new Error(body.detail ?? "consent_failed");
      }
      onRedirect(body.redirect_uri);
    } catch {
      setError("Не удалось завершить авторизацию. Запустите подключение заново");
      setSubmitting(null);
    }
  };

  return (
    <main className="min-h-screen px-5 py-8 sm:px-6">
      <div className="mx-auto flex min-h-[calc(100vh-64px)] max-w-2xl items-center justify-center">
        <section className="w-full rounded-[24px] border border-border bg-surface p-6 shadow-[0_28px_72px_rgb(var(--color-shadow)/calc(0.42*var(--shadow-opacity-scale)))] sm:p-9">
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-muted">
            OAuth · MCP
          </p>
          <h1 className="mt-4 text-3xl font-semibold text-primary">
            Разрешить доступ к Rhetorica?
          </h1>
          {error ? (
            <div className="mt-6 rounded-2xl border border-danger-border bg-danger-subtle px-4 py-3 text-sm text-danger">
              {error}
            </div>
          ) : null}
          {!details && !error ? <p className="mt-6 text-secondary">Проверяем клиента…</p> : null}
          {details ? (
            <div className="mt-7 space-y-6">
              <div className="rounded-2xl border border-border bg-surface p-5">
                <p className="text-lg font-semibold text-primary">{details.client.name}</p>
                <p className="mt-1 break-all text-xs text-secondary">{details.client.id}</p>
                <p className="mt-3 text-sm text-secondary">
                  После подтверждения браузер вернётся на {details.redirect_host ?? "клиент"}.
                </p>
              </div>
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-secondary">
                  Клиент сможет
                </h2>
                <ul className="mt-3 space-y-2">
                  {details.scopes.map((scope) => (
                    <li
                      key={scope}
                      className="rounded-xl border border-border px-4 py-3 text-sm text-primary"
                    >
                      {scopeLabels[scope] ?? scope}
                    </li>
                  ))}
                </ul>
              </div>
              <p className="text-xs leading-5 text-secondary">
                Пароль и сессионная cookie клиенту не передаются. Доступ можно отозвать, а
                короткоживущий token обновляется ротацией.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <button
                  type="button"
                  disabled={submitting !== null}
                  onClick={() => void decide("deny")}
                  className="rounded-2xl border border-border-strong px-4 py-3 font-semibold text-primary disabled:opacity-50"
                >
                  {submitting === "deny" ? "Отказываем…" : "Отказать"}
                </button>
                <button
                  type="button"
                  disabled={submitting !== null}
                  onClick={() => void decide("approve")}
                  className="rounded-2xl border border-accent bg-accent px-4 py-3 font-semibold text-on-accent disabled:opacity-50"
                >
                  {submitting === "approve" ? "Разрешаем…" : "Разрешить"}
                </button>
              </div>
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}
