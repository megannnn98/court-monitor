/** Russian words for the status codes the API returns, as the legacy UI writes them.
 * Display only: which status a record has is the server's answer. */
export const OPERATION_STATUS: Record<string, string> = {
  pending: "В очереди",
  running: "Выполняется",
  succeeded: "Завершено",
  failed: "Завершено с ошибками",
  interrupted: "Прервано"
};

export const MONITORING_RUN_STATUS: Record<string, string> = {
  running: "Выполняется",
  completed: "Завершён",
  completed_with_errors: "Завершён с ошибками",
  failed: "Ошибка",
  aborted: "Прерван"
};

export const FINDING_STATUS: Record<string, string> = {
  open: "Открыта",
  acknowledged: "Принята",
  resolved: "Закрыта"
};

export const TRIGGER: Record<string, string> = {
  schedule: "по расписанию",
  manual: "вручную",
  backfill: "догрузка",
  derived: "производный"
};

export type Tone = "neutral" | "running" | "good" | "bad";

const TONES: Record<string, Tone> = {
  pending: "running",
  running: "running",
  succeeded: "good",
  completed: "good",
  completed_with_errors: "running",
  failed: "bad",
  interrupted: "bad",
  aborted: "bad"
};

export function toneOf(status: string): Tone {
  return TONES[status] ?? "neutral";
}

export function labelOf(labels: Record<string, string>, code: string): string {
  return labels[code] ?? code;
}
