export type LlmExtraSettings = Record<string, string | number | boolean>;

const dedicatedKeys = new Set([
  "context_window_tokens", "context_window", "require_explicit_final_answer", "verify_tls",
]);

export function additionalLlmSettings(settings: LlmExtraSettings = {}): LlmExtraSettings {
  return Object.fromEntries(Object.entries(settings).filter(([key]) => !dedicatedKeys.has(key)));
}

export function parseLlmJson(value: FormDataEntryValue | null, label: string): Record<string, unknown> {
  const raw = String(value ?? "").trim();
  const parsed: unknown = raw ? JSON.parse(raw) : {};
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} должен быть объектом JSON`);
  }
  return parsed as Record<string, unknown>;
}

export function parseLlmHeaders(value: FormDataEntryValue | null): Record<string, string> {
  const parsed = parseLlmJson(value, "HTTP-заголовки");
  if (Object.entries(parsed).some(([key, value]) => !key.trim() || typeof value !== "string")) {
    throw new Error("HTTP-заголовки должны иметь непустые имена и строковые значения");
  }
  return parsed as Record<string, string>;
}

export function buildLlmExtraSettings(
  formData: FormData,
  base: LlmExtraSettings = {}
): LlmExtraSettings {
  const parsed = parseLlmJson(formData.get("llm_extra_settings_json"), "Дополнительные настройки");
  for (const [key, value] of Object.entries(parsed)) {
    if (!key.trim() || !["string", "number", "boolean"].includes(typeof value) ||
        (typeof value === "number" && !Number.isFinite(value))) {
      throw new Error("Дополнительные настройки должны иметь непустые имена и строковые, числовые или логические значения");
    }
    if (dedicatedKeys.has(key)) {
      throw new Error(`Используйте отдельное поле формы для ${key}`);
    }
  }
  const settings = parsed as LlmExtraSettings;
  if (formData.get("require_explicit_final_answer") !== null) {
    settings.require_explicit_final_answer = true;
  } else if (base.require_explicit_final_answer !== undefined) {
    settings.require_explicit_final_answer = false;
  }
  const contextWindow = String(formData.get("context_window_tokens") ?? "").trim();
  if (contextWindow) {
    const existing = base.context_window_tokens ?? base.context_window;
    if (contextWindow === String(existing)) {
      for (const key of ["context_window_tokens", "context_window"]) {
        if (base[key] !== undefined) settings[key] = base[key];
      }
    } else {
      settings.context_window_tokens = Number(contextWindow);
    }
  }
  return settings;
}
