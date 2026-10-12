import { describe, expect, it } from "vitest";
import { safeNextPath } from "./safeNextPath";

describe("safeNextPath", () => {
  it("keeps an OAuth consent path with its query", () => {
    expect(safeNextPath("/oauth/consent?transaction=rh_otx_test.secret")).toBe(
      "/oauth/consent?transaction=rh_otx_test.secret",
    );
  });

  it("rejects absolute, protocol-relative, and backslash redirects", () => {
    expect(safeNextPath("https://evil.example")).toBe("/");
    expect(safeNextPath("//evil.example/path")).toBe("/");
    expect(safeNextPath("/\\evil.example/path")).toBe("/");
  });
});
