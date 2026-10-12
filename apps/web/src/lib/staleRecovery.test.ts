import { describe, expect, it } from "vitest";

import {
  AUTO_RECOVERY_COOLDOWN_MS,
  MAX_AUTO_RECOVERY_ATTEMPTS,
  PENDING_CHAT_ACTION_KEY,
  STALE_DETECTION_FRESH_MS,
  STALE_DETECTED_KEY,
  acknowledgeStaleAsset,
  clearRecoveryState,
  getPendingChatAction,
  hasPendingChatAction,
  isRecoveryInProgress,
  isStaleDetectionFresh,
  markRecoverySettled,
  readStaleDetectedAt,
  recoveryBudgetExhausted,
  requestRecoveryReload,
  setPendingChatAction,
  takePendingChatAction,
} from "./staleRecovery";

function createStorage() {
  const map = new Map<string, string>();
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
    removeItem: (key: string) => {
      map.delete(key);
    },
  };
}

describe("stale recovery", () => {
  it("grants the first automatic reload and records an attempt", () => {
    const storage = createStorage();
    expect(requestRecoveryReload(storage, 10000)).toBe(true);
    expect(storage.getItem("__rh_recovery_attempts")).toBe("1");
    expect(isRecoveryInProgress(storage)).toBe(true);
  });

  it("denies a second reload within the cooldown", () => {
    const storage = createStorage();
    expect(requestRecoveryReload(storage, 10000)).toBe(true);
    expect(requestRecoveryReload(storage, 10000 + AUTO_RECOVERY_COOLDOWN_MS - 1)).toBe(false);
  });

  it("allows a reload after the cooldown up to the attempt limit", () => {
    const storage = createStorage();
    expect(requestRecoveryReload(storage, 10000)).toBe(true);
    expect(requestRecoveryReload(storage, 10000 + AUTO_RECOVERY_COOLDOWN_MS)).toBe(true);
    expect(recoveryBudgetExhausted(storage)).toBe(true);
  });

  it("does not allow automatic reload after the attempt limit is exhausted", () => {
    const storage = createStorage();
    expect(requestRecoveryReload(storage, 10000)).toBe(true);
    expect(requestRecoveryReload(storage, 10000 + AUTO_RECOVERY_COOLDOWN_MS)).toBe(true);
    expect(requestRecoveryReload(storage, 10000 + 2 * AUTO_RECOVERY_COOLDOWN_MS)).toBe(false);
    expect(MAX_AUTO_RECOVERY_ATTEMPTS).toBeGreaterThanOrEqual(1);
  });

  it("round-trips a pending send action and consumes it once", () => {
    const storage = createStorage();
    setPendingChatAction(storage, {
      type: "send",
      workspaceId: "default",
      conversationId: "conv-1",
      draftText: "Сохранить меня",
      hadAttachments: false,
    });
    expect(hasPendingChatAction(storage)).toBe(true);
    expect(getPendingChatAction(storage)).toEqual({
      type: "send",
      workspaceId: "default",
      conversationId: "conv-1",
      draftText: "Сохранить меня",
      hadAttachments: false,
    });
    expect(takePendingChatAction(storage)).toEqual({
      type: "send",
      workspaceId: "default",
      conversationId: "conv-1",
      draftText: "Сохранить меня",
      hadAttachments: false,
    });
    expect(hasPendingChatAction(storage)).toBe(false);
    expect(getPendingChatAction(storage)).toBeNull();
  });

  it("rejects a malformed pending action", () => {
    const storage = createStorage();
    storage.setItem(PENDING_CHAT_ACTION_KEY, "{not json");
    expect(getPendingChatAction(storage)).toBeNull();

    storage.setItem(
      PENDING_CHAT_ACTION_KEY,
      JSON.stringify({ type: "send", workspaceId: 42 })
    );
    expect(getPendingChatAction(storage)).toBeNull();
  });

  it("tracks detection freshness", () => {
    const storage = createStorage();
    expect(isStaleDetectionFresh(storage, 10000)).toBe(false);
    storage.setItem(STALE_DETECTED_KEY, "5000");
    expect(isStaleDetectionFresh(storage, 5000 + STALE_DETECTION_FRESH_MS)).toBe(true);
    expect(isStaleDetectionFresh(storage, 5000 + STALE_DETECTION_FRESH_MS + 1)).toBe(false);
    expect(readStaleDetectedAt(storage)).toBe(5000);
  });

  it("acknowledges detection and clears the whole recovery state", () => {
    const storage = createStorage();
    storage.setItem(STALE_DETECTED_KEY, "1000");
    setPendingChatAction(storage, {
      type: "switch",
      workspaceId: "default",
      targetConversationId: "conv-2",
      draftText: "draft",
      hadAttachments: false,
    });
    requestRecoveryReload(storage, 1000);

    acknowledgeStaleAsset(storage);
    expect(readStaleDetectedAt(storage)).toBe(0);

    requestRecoveryReload(storage, 1000 + AUTO_RECOVERY_COOLDOWN_MS);
    clearRecoveryState(storage);
    expect(readStaleDetectedAt(storage)).toBe(0);
    expect(hasPendingChatAction(storage)).toBe(false);
    expect(isRecoveryInProgress(storage)).toBe(false);
    expect(recoveryBudgetExhausted(storage)).toBe(false);
  });

  it("marks a settled recovery without removing the detection flag", () => {
    const storage = createStorage();
    storage.setItem(STALE_DETECTED_KEY, "1000");
    requestRecoveryReload(storage, 1000);
    markRecoverySettled(storage);
    expect(isRecoveryInProgress(storage)).toBe(false);
    expect(readStaleDetectedAt(storage)).toBe(1000);
  });
});