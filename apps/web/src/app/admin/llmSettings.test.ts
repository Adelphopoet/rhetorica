import { describe, expect, it } from "vitest";
import { additionalLlmSettings, buildLlmExtraSettings, parseLlmHeaders } from "./llmSettings";

describe("LLM settings serialization", () => {
  it("preserves extra scalar types and removes deleted keys", () => {
    const data = new FormData();
    data.set("llm_extra_settings_json", '{"thinking":false,"retries":0,"label":""}');
    expect(buildLlmExtraSettings(data, { deleted: true })).toEqual({ thinking: false, retries: 0, label: "" });
  });
  it("preserves existing context alias and type", () => {
    const data = new FormData();
    data.set("llm_extra_settings_json", "{}");
    data.set("context_window_tokens", "180000");
    expect(buildLlmExtraSettings(data, { context_window: "180000" })).toEqual({ context_window: "180000" });
    data.set("context_window_tokens", "");
    expect(buildLlmExtraSettings(data, { context_window: "180000" })).toEqual({});
  });
  it.each(['[]', '{"nested":{}}', '{"key":null}', '{"verify_tls":true}', '{"":"x"}'])("rejects invalid extra JSON: %s", (json) => {
    const data = new FormData(); data.set("llm_extra_settings_json", json);
    expect(() => buildLlmExtraSettings(data)).toThrow();
  });
  it("preserves header values and rejects malformed headers", () => {
    expect(parseLlmHeaders('{"X-Label":" value ","X-Empty":""}')).toEqual({ "X-Label": " value ", "X-Empty": "" });
    expect(() => parseLlmHeaders('{"X-Number":2}')).toThrow();
    expect(() => parseLlmHeaders('[]')).toThrow();
  });
  it("shows every additional option except those with dedicated controls", () => {
    expect(additionalLlmSettings({ context_window: 123, verify_tls: true, require_explicit_final_answer: false, custom: "x" })).toEqual({ custom: "x" });
  });
});
