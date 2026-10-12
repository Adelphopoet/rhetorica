import { describe, expect, it } from "vitest";
import { additionalLlmSettings, buildLlmExtraSettings, parseLlmHeaders } from "./llmSettings";

describe("LLM settings serialization", () => {
  it.each(["false", "true"])("serializes store=%s as a boolean", (value) => {
    const data = new FormData();
    data.set("store", value);
    expect(buildLlmExtraSettings(data)).toEqual({ store: value === "true" });
  });
  it("removes explicit store when provider default is selected", () => {
    const data = new FormData();
    data.set("store", "");
    expect(buildLlmExtraSettings(data, { store: false })).toEqual({});
    data.set("store", "invalid");
    expect(() => buildLlmExtraSettings(data)).toThrow(/режим хранения/);
  });
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
  it("serializes native compaction only for Responses with a positive threshold", () => {
    const data = new FormData();
    data.set("llm_extra_settings_json", "{}");
    data.set("endpoint_path", "/responses");
    data.set("native_compaction_enabled", "on");
    data.set("native_compaction_threshold_tokens", "1000");
    expect(buildLlmExtraSettings(data)).toEqual({
      native_compaction_enabled: true,
      native_compaction_threshold_tokens: 1000,
    });
    data.set("native_compaction_threshold_tokens", "0");
    expect(() => buildLlmExtraSettings(data)).toThrow(/positive integer/);
    data.set("native_compaction_threshold_tokens", "1000");
    data.set("endpoint_path", "/chat/completions");
    expect(() => buildLlmExtraSettings(data)).toThrow(/Responses API/);
    data.delete("native_compaction_enabled");
    expect(buildLlmExtraSettings(data, { native_compaction_enabled: true })).toEqual({
      native_compaction_enabled: false,
    });
  });
  it.each(['[]', '{"nested":{}}', '{"key":null}', '{"verify_tls":true}', '{"store":false}', '{"":"x"}'])("rejects invalid extra JSON: %s", (json) => {
    const data = new FormData(); data.set("llm_extra_settings_json", json);
    expect(() => buildLlmExtraSettings(data)).toThrow();
  });
  it("preserves header values and rejects malformed headers", () => {
    expect(parseLlmHeaders('{"X-Label":" value ","X-Empty":""}')).toEqual({ "X-Label": " value ", "X-Empty": "" });
    expect(() => parseLlmHeaders('{"X-Number":2}')).toThrow();
    expect(() => parseLlmHeaders('[]')).toThrow();
  });
  it("shows every additional option except those with dedicated controls", () => {
    expect(additionalLlmSettings({ context_window: 123, verify_tls: true, require_explicit_final_answer: false, native_compaction_enabled: true, native_compaction_threshold_tokens: 1000, store: false, custom: "x" })).toEqual({ custom: "x" });
  });
});
