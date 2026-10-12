const labels: Record<string, string> = {
  planning_read_file: "Чтение файла GitLab",
  gitlab_read_file: "Чтение файла GitLab",
  planning_list_tree: "Файлы проекта GitLab",
  gitlab_list_tree: "Файлы проекта GitLab",
  propose_coding_plan: "План разработки",
  repository_read: "Чтение файла",
  repository_search: "Поиск в проекте",
  repository_apply_patch: "Изменение файлов",
  repository_run_command: "Команда в окружении",
  workspace_skill_read: "Чтение навыка",
  run_dbt: "Проверка dbt",
  run_query: "SQL-запрос",
  submit_qa_verdict: "Заключение QA",
  task_status: "Состояние задачи",
};

export function toolDisplayName(name: string): string {
  return labels[name] ?? name.split("_").map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}
