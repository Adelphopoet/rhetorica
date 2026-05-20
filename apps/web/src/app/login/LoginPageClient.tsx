"use client";

import { FormEvent, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function toErrorMessage(error: string | null): string | null {
  if (!error) {
    return null;
  }

  switch (error) {
    case "invalid_credentials":
      return "Неверный логин или пароль.";
    case "inactive_user":
      return "Учетная запись отключена. Обратитесь к администратору.";
    case "login_proxy_failed":
    case "login_failed":
    default:
      return "Не удалось выполнить вход. Попробуйте еще раз.";
  }
}

export function LoginPageClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nextPath = searchParams.get("next") || "/";
  const errorMessage = toErrorMessage(error);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login, password }),
      });
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) {
        setError(body.detail ?? "login_failed");
        return;
      }

      router.push(nextPath);
      router.refresh();
    } catch {
      setError("login_failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen px-6 py-10">
      <div className="mx-auto flex min-h-[calc(100vh-80px)] max-w-5xl items-center justify-center">
        <section className="grid w-full overflow-hidden rounded-[32px] border border-[#1a2a42] bg-[linear-gradient(135deg,rgba(10,18,32,0.98),rgba(6,10,18,0.96))] shadow-[0_32px_80px_rgba(0,0,0,0.48)] lg:grid-cols-[1.15fr_0.85fr]">
          <div className="relative overflow-hidden border-b border-[#18263b] p-8 lg:border-b-0 lg:border-r lg:p-12">
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(93,168,255,0.18),transparent_38%),radial-gradient(circle_at_80%_20%,rgba(56,198,125,0.12),transparent_30%)]" />
            <div className="relative">
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-[#6d87ae]">
                Local Auth
              </p>
              <h1 className="mt-5 max-w-lg text-4xl font-semibold leading-tight text-[#f3f7ff]">
                Вход в рабочее пространство Rhetorica
              </h1>
              <p className="mt-5 max-w-xl text-base leading-7 text-[#9db0d0]">
                Используйте логин и пароль локальной учетной записи, чтобы войти в рабочее
                пространство.
              </p>
            </div>
          </div>

          <div className="p-8 lg:p-12">
            <form className="space-y-5" onSubmit={(event) => void handleSubmit(event)}>
              <p className="text-sm leading-6 text-[#8ea4c8]">
                Введите учетные данные, чтобы открыть аналитику, чаты и настройки вашего
                пространства.
              </p>
              <div>
                <label className="mb-2 block text-sm font-medium text-[#d9e6fb]" htmlFor="login">
                  Логин
                </label>
                <input
                  id="login"
                  name="login"
                  value={login}
                  onChange={(event) => setLogin(event.target.value)}
                  placeholder="Введите логин"
                  className="w-full rounded-2xl border border-[#1d304b] bg-[#0a1424] px-4 py-3 text-base text-[#eef4ff] outline-none transition focus:border-[#5da8ff] focus:ring-2 focus:ring-[#5da8ff]/20"
                  autoComplete="username"
                  required
                />
              </div>

              <div>
                <label className="mb-2 block text-sm font-medium text-[#d9e6fb]" htmlFor="password">
                  Пароль
                </label>
                <input
                  id="password"
                  name="password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="Введите пароль"
                  className="w-full rounded-2xl border border-[#1d304b] bg-[#0a1424] px-4 py-3 text-base text-[#eef4ff] outline-none transition focus:border-[#5da8ff] focus:ring-2 focus:ring-[#5da8ff]/20"
                  autoComplete="current-password"
                  required
                />
              </div>

              {errorMessage ? (
                <div className="rounded-2xl border border-[#6a2230] bg-[#291019] px-4 py-3 text-sm text-[#ffb4bf]">
                  {errorMessage}
                </div>
              ) : null}

              <button
                type="submit"
                disabled={isSubmitting}
                className="w-full rounded-2xl border border-[#3b78b5] bg-[linear-gradient(135deg,#5da8ff,#3c78f2)] px-4 py-3 text-sm font-semibold uppercase tracking-[0.18em] text-white transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSubmitting ? "Входим" : "Войти"}
              </button>
            </form>
          </div>
        </section>
      </div>
    </main>
  );
}
