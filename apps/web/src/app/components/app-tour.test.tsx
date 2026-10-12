import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { startAppTour } from "./app-tour";

function shim() {
  (window as Window & typeof globalThis).requestAnimationFrame ??= (
    cb: FrameRequestCallback,
  ) => setTimeout(() => cb(performance.now()), 0) as unknown as number;
  (window as Window & typeof globalThis).cancelAnimationFrame ??= (id: number) =>
    clearTimeout(id);
}

describe("app-tour", () => {
  beforeEach(() => {
    shim();
    document.body.innerHTML = "";
    window.localStorage.clear();
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("starts without throwing", () => {
    expect(() => startAppTour()).not.toThrow();
  });

  it("starts again after a previous run without throwing", () => {
    expect(() => startAppTour()).not.toThrow();
    expect(() => startAppTour()).not.toThrow();
  });

  it("starts repeatedly without throwing", () => {
    for (let i = 0; i < 5; i += 1) {
      expect(() => startAppTour()).not.toThrow();
    }
  });
});