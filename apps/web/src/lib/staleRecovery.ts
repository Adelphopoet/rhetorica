import { STALE_ASSET_EVENT, STALE_DETECTED_KEY } from "./staleAssetReload";

export { STALE_ASSET_EVENT, STALE_DETECTED_KEY } from "./staleAssetReload";

export const STALE_RECOVERY_SETTLED_EVENT = "rhetorica:stale-recovery-settled";

const RECOVERY_ATTEMPTS_KEY = "__rh_recovery_attempts";
const RECOVERY_LAST_ATTEMPT_KEY = "__rh_recovery_last_attempt_at";
const RECOVERY_IN_PROGRESS_KEY = "__rh_recovery_in_progress";
export const PENDING_CHAT_ACTION_KEY = "__rh_pending_chat_action";

export const MAX_AUTO_RECOVERY_ATTEMPTS = 2;
export const AUTO_RECOVERY_COOLDOWN_MS = 15000;
export const STALE_DETECTION_FRESH_MS = 5 * 60_000;

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export type PendingChatAction =
  | {
      type: "switch";
      workspaceId: string;
      targetConversationId: string;
      draftText: string;
      hadAttachments: boolean;
    }
  | {
      type: "send";
      workspaceId: string;
      conversationId: string | null;
      draftText: string;
      hadAttachments: boolean;
    };

function isPendingChatAction(value: unknown): value is PendingChatAction {
  if (!value || typeof value !== "object") {
    return false;
  }
  const action = value as Record<string, unknown>;
  if (action.type !== "switch" && action.type !== "send") {
    return false;
  }
  if (action.type === "switch") {
    return (
      typeof action.workspaceId === "string" &&
      typeof action.targetConversationId === "string" &&
      typeof action.draftText === "string"
    );
  }
  return (
    typeof action.workspaceId === "string" &&
    (action.conversationId === null || typeof action.conversationId === "string") &&
    typeof action.draftText === "string"
  );
}

export function readStaleDetectedAt(storage: StorageLike): number {
  return parseInt(storage.getItem(STALE_DETECTED_KEY) ?? "0", 10) || 0;
}

export function isStaleDetectionFresh(storage: StorageLike, now: number = Date.now()): boolean {
  const detectedAt = readStaleDetectedAt(storage);
  return detectedAt > 0 && now - detectedAt <= STALE_DETECTION_FRESH_MS;
}

export function acknowledgeStaleAsset(storage: StorageLike): void {
  storage.removeItem(STALE_DETECTED_KEY);
}

function readRecoveryAttempts(storage: StorageLike): number {
  return parseInt(storage.getItem(RECOVERY_ATTEMPTS_KEY) ?? "0", 10) || 0;
}

function lastRecoveryAttemptAt(storage: StorageLike): number {
  return parseInt(storage.getItem(RECOVERY_LAST_ATTEMPT_KEY) ?? "0", 10) || 0;
}

export function recoveryBudgetExhausted(storage: StorageLike): boolean {
  return readRecoveryAttempts(storage) >= MAX_AUTO_RECOVERY_ATTEMPTS;
}

export function isRecoveryInProgress(storage: StorageLike): boolean {
  return storage.getItem(RECOVERY_IN_PROGRESS_KEY) === "1";
}

export function requestRecoveryReload(storage: StorageLike, now: number = Date.now()): boolean {
  if (recoveryBudgetExhausted(storage)) {
    return false;
  }
  const lastAttemptAt = lastRecoveryAttemptAt(storage);
  if (lastAttemptAt !== 0 && now - lastAttemptAt < AUTO_RECOVERY_COOLDOWN_MS) {
    return false;
  }
  storage.setItem(RECOVERY_ATTEMPTS_KEY, String(readRecoveryAttempts(storage) + 1));
  storage.setItem(RECOVERY_LAST_ATTEMPT_KEY, String(now));
  storage.setItem(RECOVERY_IN_PROGRESS_KEY, "1");
  return true;
}

export function getPendingChatAction(storage: StorageLike): PendingChatAction | null {
  const raw = storage.getItem(PENDING_CHAT_ACTION_KEY);
  if (!raw) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    return isPendingChatAction(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function setPendingChatAction(storage: StorageLike, action: PendingChatAction): void {
  storage.setItem(PENDING_CHAT_ACTION_KEY, JSON.stringify(action));
}

export function hasPendingChatAction(storage: StorageLike): boolean {
  return getPendingChatAction(storage) !== null;
}

export function takePendingChatAction(storage: StorageLike): PendingChatAction | null {
  const pending = getPendingChatAction(storage);
  if (pending) {
    storage.removeItem(PENDING_CHAT_ACTION_KEY);
  }
  return pending;
}

export function markRecoverySettled(storage: StorageLike): void {
  storage.removeItem(RECOVERY_IN_PROGRESS_KEY);
}

export function clearRecoveryState(storage: StorageLike): void {
  storage.removeItem(STALE_DETECTED_KEY);
  storage.removeItem(RECOVERY_ATTEMPTS_KEY);
  storage.removeItem(RECOVERY_LAST_ATTEMPT_KEY);
  storage.removeItem(RECOVERY_IN_PROGRESS_KEY);
  storage.removeItem(PENDING_CHAT_ACTION_KEY);
}

export function emitStaleRecoverySettled(): void {
  window.dispatchEvent(new CustomEvent(STALE_RECOVERY_SETTLED_EVENT));
}

export function onStaleAssetConfirmed(handler: () => void): () => void {
  window.addEventListener(STALE_ASSET_EVENT, handler);
  return () => window.removeEventListener(STALE_ASSET_EVENT, handler);
}

export function onStaleRecoverySettled(handler: () => void): () => void {
  window.addEventListener(STALE_RECOVERY_SETTLED_EVENT, handler);
  return () => window.removeEventListener(STALE_RECOVERY_SETTLED_EVENT, handler);
}