// Presentation labels only. API values and form option values remain unchanged.
const labels: Record<string, string> = {
  success: "успешно",
  succeeded: "успешно",
  failed: "ошибка",
  running: "выполняется",
  active: "активен",
  inactive: "неактивен",
  valid: "проверен",
  invalid: "ошибка проверки",
  pending: "ожидает проверки",
  draft: "черновик",
  published: "опубликован",
  archived: "в архиве",
  owner: "владелец",
  analyst: "аналитик",
  viewer: "наблюдатель",
  admin: "администратор",
  chat_instruction: "инструкция для чата",
  any: "любая",
};

export function adminLabel(value: string) {
  return labels[value] ?? value;
}
