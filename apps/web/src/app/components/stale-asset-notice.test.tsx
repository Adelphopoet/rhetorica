import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Providers } from "./providers";
import { STALE_ASSET_EVENT, STALE_DETECTED_KEY } from "@/lib/staleAssetReload";
import { PENDING_CHAT_ACTION_KEY, emitStaleRecoverySettled } from "@/lib/staleRecovery";

function stubLocationReload() {
  const reloadSpy = vi.fn();
  const original = window.location;
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      href: original.href,
      pathname: original.pathname,
      reload: reloadSpy,
    },
  });
  return () => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: original,
    });
  };
}

describe("StaleAssetNotice", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    window.sessionStorage.clear();
  });

  it("does not render without a fresh stale assets detection", () => {
    render(<Providers>{null}</Providers>);
    expect(screen.queryByTestId("stale-asset-notice")).not.toBeInTheDocument();
  });

  it("shows the update notice and reloads via the explicit button", async () => {
    const restoreLocation = stubLocationReload();
    try {
      window.sessionStorage.setItem(STALE_DETECTED_KEY, String(Date.now()));

      render(<Providers>{null}</Providers>);

      expect(await screen.findByTestId("stale-asset-notice")).toBeInTheDocument();
      expect(screen.getByText("Приложение обновилось")).toBeInTheDocument();
      expect(
        screen.getByText(/Для корректной работы обновите страницу/)
      ).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
      expect(window.location.reload).toHaveBeenCalledTimes(1);
    } finally {
      restoreLocation();
    }
  });

  it("stays hidden while an automatic chat recovery is in progress", async () => {
    window.sessionStorage.setItem(STALE_DETECTED_KEY, String(Date.now()));
    window.sessionStorage.setItem(
      PENDING_CHAT_ACTION_KEY,
      JSON.stringify({
        type: "switch",
        workspaceId: "default",
        targetConversationId: "conv-1",
        draftText: "draft",
        hadAttachments: false,
      })
    );

    render(<Providers>{null}</Providers>);

    await waitFor(() => {
      expect(screen.queryByTestId("stale-asset-notice")).not.toBeInTheDocument();
    });
  });

  it("shows again after the recovery settles without a successful restore", () => {
    window.sessionStorage.setItem(STALE_DETECTED_KEY, String(Date.now()));
    render(<Providers>{null}</Providers>);

    const staleEvent = new CustomEvent(STALE_ASSET_EVENT);
    window.dispatchEvent(staleEvent);
    emitStaleRecoverySettled();

    expect(screen.getByTestId("stale-asset-notice")).toBeInTheDocument();
  });
});