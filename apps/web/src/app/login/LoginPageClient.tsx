"use client";

import { ThemeSelector } from "@/features/theme/ThemeSelector";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { useSearchParams } from "next/navigation";
import { safeNextPath } from "@/lib/safeNextPath";

function toErrorMessage(error: string | null): string | null {
  if (!error) {
    return null;
  }

  switch (error) {
    case "invalid_credentials":
      return "Неверный логин или пароль";
    case "inactive_user":
      return "Учетная запись отключена. Обратитесь к администратору";
    case "keycloak_group_access_denied":
      return "Нет доступа к Rhetorica. Проверьте настройки Keycloak";
    case "invalid_keycloak_state":
      return "Сессия входа устарела. Попробуйте еще раз";
    case "keycloak_auth_misconfigured":
      return "Keycloak не настроен. Проверьте переменные окружения";
    case "login_proxy_failed":
    case "login_failed":
    default:
      return "Не удалось выполнить вход. Попробуйте еще раз";
  }
}

export function LoginPageClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [mode, setMode] = useState<"keycloak" | "local">("keycloak");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const nextPath = safeNextPath(searchParams.get("next"));
  const error = searchParams.get("error");
  const errorMessage = toErrorMessage(localError ?? error);
  const keycloakLoginHref = `/api/auth/keycloak/login?next=${encodeURIComponent(nextPath)}`;

  const handleLocalSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSubmitting(true);
    setLocalError(null);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login, password }),
      });
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) {
        setLocalError(body.detail ?? "login_failed");
        return;
      }

      router.push(nextPath);
      router.refresh();
    } catch {
      setLocalError("login_failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen px-5 py-8 sm:px-6">
      <div className="mx-auto flex min-h-[calc(100vh-64px)] max-w-6xl items-center justify-center">
        <section className="grid w-full overflow-hidden rounded-[24px] border border-border bg-[linear-gradient(135deg,rgb(var(--color-surface)/0.98),rgb(var(--color-page)/0.96))] shadow-[0_28px_72px_rgb(var(--color-shadow)/calc(0.42*var(--shadow-opacity-scale)))] lg:grid-cols-[1fr_420px]">
          <div className="relative min-h-[320px] overflow-hidden border-b border-border p-8 lg:border-b-0 lg:border-r lg:p-12">
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgb(var(--color-accent)/0.2),transparent_34%),radial-gradient(circle_at_72%_22%,rgb(var(--color-success)/0.13),transparent_28%)]" />
            <div className="relative">
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-muted">
                Rhetorica
              </p>
              <h1 className="mt-5 max-w-xl text-4xl font-semibold leading-tight text-primary sm:text-5xl">
                Вход в рабочее пространство Rhetorica
              </h1>
              <p className="mt-5 max-w-xl text-base leading-7 text-secondary">
                Используйте корпоративную учетную запись Keycloak или локальный fallback-доступ.
              </p>
            </div>
          </div>

          <div className="p-6 sm:p-8 lg:p-10">
            <div className="space-y-5">
              <div className="flex justify-end"><ThemeSelector /></div>
              <div className="grid grid-cols-2 rounded-2xl border border-border bg-surface p-1">
                <button
                  type="button"
                  onClick={() => setMode("keycloak")}
                  className={`rounded-xl px-3 py-2 text-sm font-semibold transition ${
                    mode === "keycloak"
                      ? "bg-accent text-on-accent"
                      : "text-secondary hover:text-primary"
                  }`}
                >
                  Keycloak
                </button>
                <button
                  type="button"
                  onClick={() => setMode("local")}
                  className={`rounded-xl px-3 py-2 text-sm font-semibold transition ${
                    mode === "local"
                      ? "bg-accent text-on-accent"
                      : "text-secondary hover:text-primary"
                  }`}
                >
                  Локально
                </button>
              </div>

              {errorMessage ? (
                <div className="rounded-2xl border border-danger-border bg-danger-subtle px-4 py-3 text-sm text-danger">
                  {errorMessage}
                </div>
              ) : null}

              {mode === "keycloak" ? (
                <div className="space-y-5">
                  <p className="text-sm leading-6 text-secondary">
                    Доступ выдаётся после успешной авторизации в Keycloak. Админская роль
                    определяется отдельной группой.
                  </p>
                  <a
                    href={keycloakLoginHref}
                    className="block w-full rounded-2xl border border-accent bg-[linear-gradient(135deg,rgb(var(--color-accent)),rgb(var(--color-accent)))] px-4 py-3 text-center text-sm font-semibold uppercase tracking-[0.18em] text-on-accent transition hover:brightness-110"
                  >
                    Войти через Keycloak
                  </a>
                </div>
              ) : (
                <form className="space-y-4" onSubmit={(event) => void handleLocalSubmit(event)}>
                  <div>
                    <label className="mb-2 block text-sm font-medium text-primary" htmlFor="login">
                      Логин
                    </label>
                    <input
                      id="login"
                      name="login"
                      value={login}
                      onChange={(event) => setLogin(event.target.value)}
                      placeholder="Введите логин"
                      className="w-full rounded-2xl border border-control-border bg-surface px-4 py-3 text-base text-primary outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/20"
                      autoComplete="username"
                      required
                    />
                  </div>

                  <div>
                    <label
                      className="mb-2 block text-sm font-medium text-primary"
                      htmlFor="password"
                    >
                      Пароль
                    </label>
                    <input
                      id="password"
                      name="password"
                      type="password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      placeholder="Введите пароль"
                      className="w-full rounded-2xl border border-control-border bg-surface px-4 py-3 text-base text-primary outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/20"
                      autoComplete="current-password"
                      required
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={isSubmitting}
                    className="w-full rounded-2xl border border-accent bg-[linear-gradient(135deg,rgb(var(--color-accent)),rgb(var(--color-accent)))] px-4 py-3 text-sm font-semibold uppercase tracking-[0.18em] text-on-accent transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {isSubmitting ? "Входим" : "Войти локально"}
                  </button>
                </form>
              )}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
