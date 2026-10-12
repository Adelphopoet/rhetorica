import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  STALE_ASSET_GUARD_INLINE_SCRIPT,
  STALE_ASSET_EVENT,
  STALE_DETECTED_KEY,
  isConfirmedStaleAssetFailure,
  isStaleAssetUrl,
  isStaleChunkError,
} from "./staleAssetReload";

describe("stale asset detection predicates", () => {
  describe("isStaleChunkError", () => {
    it("matches dynamically imported module failures", () => {
      expect(
        isStaleChunkError(
          "Failed to fetch dynamically imported module: https://app/_next/static/chunks/app/page.js"
        )
      ).toBe(true);
    });

    it("matches webpack ChunkLoadError and loading chunk failures", () => {
      expect(isStaleChunkError("ChunkLoadError: Loading chunk 123 failed.")).toBe(true);
      expect(isStaleChunkError("Loading chunk 7 failed.")).toBe(true);
    });

    it("matches Turbopack module script failures", () => {
      expect(isStaleChunkError("Importing a module script failed.")).toBe(true);
    });

    it("ignores API and network errors that are not stale assets", () => {
      expect(isStaleChunkError("TypeError: undefined is not an object")).toBe(false);
      expect(isStaleChunkError("Failed to fetch /api/chat/ask/stream")).toBe(false);
      expect(isStaleChunkError("NetworkError when attempting to fetch resource.")).toBe(false);
      expect(isStaleChunkError(undefined)).toBe(false);
      expect(isStaleChunkError("")).toBe(false);
    });
  });

  describe("isStaleAssetUrl", () => {
    it("matches chunk and style asset URLs", () => {
      expect(isStaleAssetUrl("/_next/static/chunks/app/page.js")).toBe(true);
      expect(isStaleAssetUrl("https://app/_next/static/css/auth.css")).toBe(true);
    });

    it("ignores media, fonts, APIs and unrelated URLs", () => {
      expect(isStaleAssetUrl("/_next/static/media/logo.svg")).toBe(false);
      expect(isStaleAssetUrl("/api/chat/ask/stream")).toBe(false);
      expect(isStaleAssetUrl(null)).toBe(false);
      expect(isStaleAssetUrl("")).toBe(false);
    });
  });

  describe("isConfirmedStaleAssetFailure", () => {
    it("confirms a failure whose reported URL is a chunk asset", () => {
      expect(
        isConfirmedStaleAssetFailure(
          "Failed to fetch dynamically imported module: https://app/_next/static/chunks/app/page.js",
          null
        )
      ).toBe(true);
      expect(
        isConfirmedStaleAssetFailure("ChunkLoadError: Loading chunk 7 failed.\n(https://app/_next/static/chunks/7.js)", null)
      ).toBe(true);
    });

    it("rejects a dynamic import failure reported for a non-chunk URL", () => {
      expect(
        isConfirmedStaleAssetFailure(
          "Failed to fetch dynamically imported module: https://app/assets/custom-widget.js",
          null
        )
      ).toBe(false);
    });

    it("rejects a chunk-shaped failure when no URL can be reliably known", () => {
      expect(isConfirmedStaleAssetFailure("Importing a module script failed.", null)).toBe(false);
      expect(isConfirmedStaleAssetFailure("Loading chunk 7 failed.", null)).toBe(false);
      expect(isConfirmedStaleAssetFailure("Importing a module script failed.", "")).toBe(false);
    });

    it("falls back to the reporting script URL only when the message carries none", () => {
      expect(
        isConfirmedStaleAssetFailure(
          "Importing a module script failed.",
          "https://app/_next/static/chunks/main-app.js"
        )
      ).toBe(true);
      expect(
        isConfirmedStaleAssetFailure("Importing a module script failed.", "https://app/legacy/main.js")
      ).toBe(false);
    });

    it("trusts the reported URL over the reporting script URL", () => {
      expect(
        isConfirmedStaleAssetFailure(
          "Failed to fetch dynamically imported module: https://app/assets/custom-widget.js",
          "https://app/_next/static/chunks/main-app.js"
        )
      ).toBe(false);
    });

    it("rejects unrelated errors regardless of script URL", () => {
      expect(
        isConfirmedStaleAssetFailure("Failed to fetch /api/chat/ask/stream", "https://app/_next/static/chunks/main-app.js")
      ).toBe(false);
      expect(isConfirmedStaleAssetFailure(undefined, "https://app/_next/static/chunks/main-app.js")).toBe(false);
    });
  });
});

function installGuardIn(win: Window) {
  const evaluate = new Function(STALE_ASSET_GUARD_INLINE_SCRIPT) as () => void;
  evaluate.call(win);
}

function dispatchRejection(reason: unknown) {
  const event = new Event("unhandledrejection");
  Object.defineProperty(event, "reason", { value: reason });
  window.dispatchEvent(event);
}

describe("stale asset guard inline script", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("reports a confirmed stale chunk without navigating away", () => {
    const beforeUnload = vi.fn();
    window.addEventListener("beforeunload", beforeUnload);
    const received: Event[] = [];
    window.addEventListener(STALE_ASSET_EVENT, (event) => received.push(event));

    installGuardIn(window);
    window.dispatchEvent(
      new ErrorEvent("error", {
        message: "Failed to fetch dynamically imported module: https://app/_next/static/chunks/x.js",
      })
    );

    expect(window.sessionStorage.getItem(STALE_DETECTED_KEY)).not.toBeNull();
    expect(received).toHaveLength(1);
    expect(beforeUnload).not.toHaveBeenCalled();
  });

  it("does not report ordinary API or network errors as stale assets", () => {
    const beforeUnload = vi.fn();
    window.addEventListener("beforeunload", beforeUnload);
    const received: Event[] = [];
    window.addEventListener(STALE_ASSET_EVENT, (event) => received.push(event));

    installGuardIn(window);
    window.dispatchEvent(
      new ErrorEvent("error", {
        message: "Failed to fetch /api/chat/ask/stream",
      })
    );

    expect(window.sessionStorage.getItem(STALE_DETECTED_KEY)).toBeNull();
    expect(received).toHaveLength(0);
    expect(beforeUnload).not.toHaveBeenCalled();
  });

  it("reports CSS chunk load failures on stylesheet links", () => {
    installGuardIn(window);
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/_next/static/css/app.css";
    document.body.appendChild(link);
    try {
      window.dispatchEvent(new Event("error"));
      link.dispatchEvent(new Event("error"));
    } finally {
      link.remove();
    }

    expect(window.sessionStorage.getItem(STALE_DETECTED_KEY)).not.toBeNull();
  });

  it("does not report a dynamic import failure from a non-chunk URL", () => {
    const received: Event[] = [];
    window.addEventListener(STALE_ASSET_EVENT, (event) => received.push(event));

    installGuardIn(window);
    window.dispatchEvent(
      new ErrorEvent("error", {
        message: "Failed to fetch dynamically imported module: https://app/assets/custom-widget.js",
        filename: "https://app/_next/static/chunks/main-app.js",
      })
    );

    expect(window.sessionStorage.getItem(STALE_DETECTED_KEY)).toBeNull();
    expect(received).toHaveLength(0);
  });

  it("does not report a rejection whose failure carries no URL", () => {
    const received: Event[] = [];
    window.addEventListener(STALE_ASSET_EVENT, (event) => received.push(event));

    installGuardIn(window);
    dispatchRejection(new Error("Importing a module script failed."));

    expect(window.sessionStorage.getItem(STALE_DETECTED_KEY)).toBeNull();
    expect(received).toHaveLength(0);
  });

  it("reports a rejection that names a chunk URL", () => {
    installGuardIn(window);
    dispatchRejection(
      new Error("Failed to fetch dynamically imported module: https://app/_next/static/chunks/lazy.js")
    );

    expect(window.sessionStorage.getItem(STALE_DETECTED_KEY)).not.toBeNull();
  });

  it("reports a chunk-shaped error whose reporting script is a chunk URL", () => {
    installGuardIn(window);
    window.dispatchEvent(
      new ErrorEvent("error", {
        message: "Importing a module script failed.",
        filename: "https://app/_next/static/chunks/main-app.js",
      })
    );

    expect(window.sessionStorage.getItem(STALE_DETECTED_KEY)).not.toBeNull();
  });
});