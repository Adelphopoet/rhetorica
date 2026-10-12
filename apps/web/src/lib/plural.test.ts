import { describe, expect, it } from "vitest";
import { pluralize } from "./plural";

const THREE_FORMS = ["сценарий", "сценария", "сценариев"] as const;
const TWO_FORMS = ["сценарий", "сценариев"] as const;

describe("pluralize", () => {
  it.each([
    [1, "сценарий"],
    [21, "сценарий"],
    [101, "сценарий"],
    [2, "сценария"],
    [4, "сценария"],
    [22, "сценария"],
    [5, "сценариев"],
    [11, "сценариев"],
    [12, "сценариев"],
    [14, "сценариев"],
    [15, "сценариев"],
    [25, "сценариев"],
  ])("pluralize(%i) -> %s (3 forms)", (count, expected) => {
    expect(pluralize(count, THREE_FORMS)).toBe(expected);
  });

  it("handles zero as many (3 forms)", () => {
    expect(pluralize(0, THREE_FORMS)).toBe("сценариев");
  });

  it("handles negative counts (3 forms)", () => {
    expect(pluralize(-2, THREE_FORMS)).toBe("сценария");
  });

  it("supports 2 forms", () => {
    expect(pluralize(1, TWO_FORMS)).toBe("сценарий");
    expect(pluralize(2, TWO_FORMS)).toBe("сценариев");
    expect(pluralize(5, TWO_FORMS)).toBe("сценариев");
    expect(pluralize(0, TWO_FORMS)).toBe("сценариев");
  });
});