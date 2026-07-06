import { describe, expect, it } from "vitest";

import {
  buildConfig,
  buildCreatePayload,
  buildUpdatePayload,
  emptyForm,
  formFromProvider,
  hasErrors,
  KIND_CATALOG,
  KIND_ORDER,
  typeForKind,
  validateForm,
  withKind,
  type ProviderFormValues,
  type ProviderRead,
} from "../pages/providers/formPayload";

describe("KIND_CATALOG / typeForKind", () => {
  it("7 種すべてが定義され、順序リストと一致する", () => {
    expect(KIND_ORDER).toHaveLength(7);
    expect(Object.keys(KIND_CATALOG).sort()).toEqual([...KIND_ORDER].sort());
  });

  it("kind → type の写像が KIND_BY_TYPE と一致する", () => {
    expect(typeForKind("openai_compatible")).toBe("llm");
    expect(typeForKind("anthropic")).toBe("llm");
    expect(typeForKind("gemini")).toBe("llm");
    expect(typeForKind("voicevox")).toBe("tts");
    expect(typeForKind("openjtalk")).toBe("tts");
    expect(typeForKind("whisper")).toBe("stt");
    expect(typeForKind("google_stt")).toBe("stt");
  });

  it("api_key を使う kind は registry と一致（tts と google_stt は false）", () => {
    expect(KIND_CATALOG.openai_compatible.usesApiKey).toBe(true);
    expect(KIND_CATALOG.anthropic.usesApiKey).toBe(true);
    expect(KIND_CATALOG.gemini.usesApiKey).toBe(true);
    expect(KIND_CATALOG.whisper.usesApiKey).toBe(true);
    expect(KIND_CATALOG.voicevox.usesApiKey).toBe(false);
    expect(KIND_CATALOG.openjtalk.usesApiKey).toBe(false);
    expect(KIND_CATALOG.google_stt.usesApiKey).toBe(false);
  });

  it("config フィールドのキーが registry の cfg.get キーと一致する", () => {
    const keys = (k: keyof typeof KIND_CATALOG) => KIND_CATALOG[k].fields.map((f) => f.key);
    expect(keys("openai_compatible")).toEqual(["base_url", "model", "temperature", "max_tokens"]);
    expect(keys("anthropic")).toEqual(["model", "max_tokens"]);
    expect(keys("gemini")).toEqual(["model", "temperature"]);
    expect(keys("voicevox")).toEqual(["engine_url", "speaker"]);
    expect(keys("openjtalk")).toEqual(["dict_dir", "voice_path"]);
    expect(keys("whisper")).toEqual(["model", "language"]);
    expect(keys("google_stt")).toEqual(["project", "location", "language", "model"]);
  });
});

describe("emptyForm / withKind", () => {
  it("emptyForm は選んだ kind の config キーを空文字で持つ", () => {
    const form = emptyForm("anthropic");
    expect(form.kind).toBe("anthropic");
    expect(form.config).toEqual({ model: "", max_tokens: "" });
    expect(form.enabled).toBe(true);
  });

  it("withKind は config を新 kind のキーで作り直し、同名キーの値だけ引き継ぐ", () => {
    let form = emptyForm("openai_compatible");
    form = { ...form, config: { ...form.config, model: "gpt-4o", temperature: "0.5" } };
    const next = withKind(form, "gemini");
    expect(next.kind).toBe("gemini");
    // gemini は model / temperature を持つので両方引き継がれる
    expect(next.config).toEqual({ model: "gpt-4o", temperature: "0.5" });
  });

  it("withKind で共通キーのない kind へ切替えると値は落ちる", () => {
    let form = emptyForm("openai_compatible");
    form = { ...form, config: { ...form.config, model: "gpt-4o" } };
    const next = withKind(form, "voicevox");
    expect(next.config).toEqual({ engine_url: "", speaker: "" });
  });
});

describe("buildConfig", () => {
  it("空フィールドを省き、number フィールドを数値化する", () => {
    const form: ProviderFormValues = {
      name: "x",
      kind: "openai_compatible",
      api_key: "",
      enabled: true,
      config: { base_url: "  https://x/v1  ", model: "", temperature: "0.3", max_tokens: "" },
    };
    expect(buildConfig(form)).toEqual({ base_url: "https://x/v1", temperature: 0.3 });
  });

  it("voicevox の speaker を数値化する", () => {
    const form = { ...emptyForm("voicevox"), config: { engine_url: "", speaker: "3" } };
    expect(buildConfig(form)).toEqual({ speaker: 3 });
  });
});

describe("buildCreatePayload", () => {
  it("type を kind から導出し、api_key は非空時のみ含める", () => {
    const form: ProviderFormValues = {
      name: "  my-gpt  ",
      kind: "openai_compatible",
      api_key: "sk-secret",
      enabled: true,
      config: { base_url: "", model: "gpt-4o", temperature: "", max_tokens: "" },
    };
    expect(buildCreatePayload(form)).toEqual({
      name: "my-gpt",
      type: "llm",
      kind: "openai_compatible",
      config: { model: "gpt-4o" },
      enabled: true,
      api_key: "sk-secret",
    });
  });

  it("api_key が空なら payload に含めない", () => {
    const form = { ...emptyForm("voicevox"), name: "vv", config: { engine_url: "", speaker: "1" } };
    const payload = buildCreatePayload(form);
    expect("api_key" in payload).toBe(false);
    expect(payload).toMatchObject({ type: "tts", kind: "voicevox", config: { speaker: 1 } });
  });
});

const originalProvider: ProviderRead = {
  id: 1,
  name: "my-gpt",
  type: "llm",
  kind: "openai_compatible",
  config: { model: "gpt-4o", temperature: 0.7 },
  api_key_masked: "sk-…cret",
  enabled: true,
};

describe("formFromProvider", () => {
  it("config を文字列化し、api_key は空にする", () => {
    const form = formFromProvider(originalProvider);
    expect(form.name).toBe("my-gpt");
    expect(form.kind).toBe("openai_compatible");
    expect(form.api_key).toBe("");
    expect(form.config).toEqual({ base_url: "", model: "gpt-4o", temperature: "0.7", max_tokens: "" });
  });
});

describe("buildUpdatePayload", () => {
  it("無変更なら空オブジェクト", () => {
    const form = formFromProvider(originalProvider);
    expect(buildUpdatePayload(form, originalProvider)).toEqual({});
  });

  it("api_key は空なら含めない（据え置き）", () => {
    const form = formFromProvider(originalProvider);
    expect("api_key" in buildUpdatePayload(form, originalProvider)).toBe(false);
  });

  it("api_key を入力したら含める", () => {
    const form = { ...formFromProvider(originalProvider), api_key: "sk-new" };
    expect(buildUpdatePayload(form, originalProvider)).toEqual({ api_key: "sk-new" });
  });

  it("config を変更したら config 全体を含める", () => {
    const form = formFromProvider(originalProvider);
    form.config.temperature = "0.9";
    expect(buildUpdatePayload(form, originalProvider)).toEqual({
      config: { model: "gpt-4o", temperature: 0.9 },
    });
  });

  it("name / enabled の変更を個別に反映する", () => {
    const form = { ...formFromProvider(originalProvider), name: "renamed", enabled: false };
    expect(buildUpdatePayload(form, originalProvider)).toEqual({ name: "renamed", enabled: false });
  });

  it("name が空なら含めない", () => {
    const form = { ...formFromProvider(originalProvider), name: "   " };
    expect("name" in buildUpdatePayload(form, originalProvider)).toBe(false);
  });
});

describe("validateForm / hasErrors", () => {
  it("作成時に名前必須", () => {
    const form = emptyForm("gemini");
    const errors = validateForm(form, "create");
    expect(errors.name).toBeDefined();
    expect(hasErrors(errors)).toBe(true);
  });

  it("編集時は名前空でもエラーにしない", () => {
    const form = { ...emptyForm("gemini"), name: "" };
    expect(validateForm(form, "edit").name).toBeUndefined();
  });

  it("number フィールドに非数値を入れるとエラー", () => {
    const form = { ...emptyForm("openai_compatible"), name: "x" };
    form.config.temperature = "abc";
    const errors = validateForm(form, "create");
    expect(errors.config.temperature).toBeDefined();
    expect(hasErrors(errors)).toBe(true);
  });

  it("正常な入力ならエラーなし", () => {
    const form = { ...emptyForm("openai_compatible"), name: "x" };
    form.config.temperature = "0.7";
    form.config.max_tokens = "500";
    const errors = validateForm(form, "create");
    expect(hasErrors(errors)).toBe(false);
  });
});
