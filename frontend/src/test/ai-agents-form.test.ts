import { describe, expect, it } from "vitest";

import {
  buildCreatePayload,
  buildUpdatePayload,
  validateForm,
  type AiAgentFormValues,
  type AiAgentRead,
} from "../pages/ai-agents/formPayload";

const original: AiAgentRead = {
  id: 1,
  name: "受付 AI",
  number: null,
  system_prompt: "あなたは受付担当です。",
  greeting: "お電話ありがとうございます。",
  llm_provider_id: 10,
  tts_provider_id: 20,
  stt_provider_id: 30,
  max_history: 10,
  silence_end_ms: 600,
  enabled: true,
};

function formOf(overrides: Partial<AiAgentFormValues>): AiAgentFormValues {
  return {
    name: "受付 AI",
    number: "",
    system_prompt: "あなたは受付担当です。",
    greeting: "お電話ありがとうございます。",
    llm_provider_id: 10,
    tts_provider_id: 20,
    stt_provider_id: 30,
    max_history: 10,
    silence_end_ms: 600,
    enabled: true,
    ...overrides,
  };
}

describe("buildCreatePayload", () => {
  it("全フィールドを含む payload を返す", () => {
    const payload = buildCreatePayload(formOf({}));
    expect(payload).toEqual({
      name: "受付 AI",
      system_prompt: "あなたは受付担当です。",
      greeting: "お電話ありがとうございます。",
      llm_provider_id: 10,
      tts_provider_id: 20,
      stt_provider_id: 30,
      max_history: 10,
      silence_end_ms: 600,
      enabled: true,
    });
  });

  it("name の前後の空白を除去する", () => {
    const payload = buildCreatePayload(formOf({ name: "  受付 AI  " }));
    expect(payload.name).toBe("受付 AI");
  });

  it("system_prompt が空文字でも含める", () => {
    const payload = buildCreatePayload(formOf({ system_prompt: "" }));
    expect(payload.system_prompt).toBe("");
  });

  it("number が空なら payload に含めない（番号なし）", () => {
    const payload = buildCreatePayload(formOf({ number: "" }));
    expect("number" in payload).toBe(false);
  });

  it("number が非空なら trim して含める", () => {
    const payload = buildCreatePayload(formOf({ number: " 600 " }));
    expect(payload.number).toBe("600");
  });
});

describe("buildUpdatePayload（編集フォーム → PATCH payload 変換）", () => {
  it("変更がなければ空 payload", () => {
    expect(buildUpdatePayload(formOf({}), original)).toEqual({});
  });

  it("name を変更したとき name を含める", () => {
    expect(buildUpdatePayload(formOf({ name: "新エージェント" }), original)).toEqual({
      name: "新エージェント",
    });
  });

  it("name が空文字なら含めない（据え置き）", () => {
    expect(buildUpdatePayload(formOf({ name: "" }), original)).toEqual({});
  });

  it("number を設定したとき含める", () => {
    expect(buildUpdatePayload(formOf({ number: "600" }), original)).toEqual({ number: "600" });
  });

  it("number が unchanged（null ↔ 空文字）なら含めない", () => {
    expect(buildUpdatePayload(formOf({ number: "" }), original)).toEqual({});
    expect(
      buildUpdatePayload(formOf({ number: "600" }), { ...original, number: "600" }),
    ).toEqual({});
  });

  it("number を空文字にしたとき含める（\"\" = 番号を外す）", () => {
    expect(
      buildUpdatePayload(formOf({ number: "" }), { ...original, number: "600" }),
    ).toEqual({ number: "" });
  });

  it("system_prompt を変更したとき含める", () => {
    expect(buildUpdatePayload(formOf({ system_prompt: "新しいプロンプト" }), original)).toEqual({
      system_prompt: "新しいプロンプト",
    });
  });

  it("system_prompt を空文字にしたとき含める（空文字は有効値）", () => {
    expect(buildUpdatePayload(formOf({ system_prompt: "" }), original)).toEqual({
      system_prompt: "",
    });
  });

  it("llm_provider_id を変更したとき含める", () => {
    expect(buildUpdatePayload(formOf({ llm_provider_id: 99 }), original)).toEqual({
      llm_provider_id: 99,
    });
  });

  it("llm_provider_id が null なら含めない", () => {
    // null は「未選択」状態。更新時は据え置き。
    expect(buildUpdatePayload(formOf({ llm_provider_id: null }), original)).toEqual({});
  });

  it("max_history を変更したとき含める", () => {
    expect(buildUpdatePayload(formOf({ max_history: 20 }), original)).toEqual({
      max_history: 20,
    });
  });

  it("silence_end_ms を変更したとき含める", () => {
    expect(buildUpdatePayload(formOf({ silence_end_ms: 800 }), original)).toEqual({
      silence_end_ms: 800,
    });
  });

  it("enabled を切り替えたとき含める", () => {
    expect(buildUpdatePayload(formOf({ enabled: false }), original)).toEqual({ enabled: false });
  });

  it("複数フィールドを変更したとき全て含める", () => {
    expect(
      buildUpdatePayload(
        formOf({ name: "夜間 AI", max_history: 5, enabled: false }),
        original,
      ),
    ).toEqual({ name: "夜間 AI", max_history: 5, enabled: false });
  });
});

describe("validateForm", () => {
  it("有効なフォームはエラーなし", () => {
    expect(Object.keys(validateForm(formOf({}), "create"))).toHaveLength(0);
  });

  it("name が空ならエラー", () => {
    expect(validateForm(formOf({ name: "  " }), "create").name).toBeTruthy();
  });

  it("name が 101 文字以上ならエラー", () => {
    expect(validateForm(formOf({ name: "a".repeat(101) }), "create").name).toBeTruthy();
  });

  it("number は空 or 数字 2〜6 桁のみ許可する", () => {
    expect(validateForm(formOf({ number: "" }), "create").number).toBeUndefined();
    expect(validateForm(formOf({ number: "600" }), "create").number).toBeUndefined();
    expect(validateForm(formOf({ number: "1" }), "create").number).toBeTruthy();
    expect(validateForm(formOf({ number: "1234567" }), "create").number).toBeTruthy();
    expect(validateForm(formOf({ number: "60a" }), "create").number).toBeTruthy();
  });

  it("llm_provider_id が null ならエラー", () => {
    expect(validateForm(formOf({ llm_provider_id: null }), "create").llm_provider_id).toBeTruthy();
  });

  it("tts_provider_id が null ならエラー", () => {
    expect(validateForm(formOf({ tts_provider_id: null }), "create").tts_provider_id).toBeTruthy();
  });

  it("stt_provider_id が null ならエラー", () => {
    expect(validateForm(formOf({ stt_provider_id: null }), "create").stt_provider_id).toBeTruthy();
  });

  it("max_history が 0 ならエラー（下限 1）", () => {
    expect(validateForm(formOf({ max_history: 0 }), "create").max_history).toBeTruthy();
  });

  it("max_history が 51 ならエラー（上限 50）", () => {
    expect(validateForm(formOf({ max_history: 51 }), "create").max_history).toBeTruthy();
  });

  it("silence_end_ms が 199 ならエラー（下限 200）", () => {
    expect(validateForm(formOf({ silence_end_ms: 199 }), "create").silence_end_ms).toBeTruthy();
  });

  it("silence_end_ms が 3001 ならエラー（上限 3000）", () => {
    expect(validateForm(formOf({ silence_end_ms: 3001 }), "create").silence_end_ms).toBeTruthy();
  });

  it("境界値 max_history=1, 50 はエラーなし", () => {
    expect(validateForm(formOf({ max_history: 1 }), "create").max_history).toBeUndefined();
    expect(validateForm(formOf({ max_history: 50 }), "create").max_history).toBeUndefined();
  });

  it("境界値 silence_end_ms=200, 3000 はエラーなし", () => {
    expect(validateForm(formOf({ silence_end_ms: 200 }), "create").silence_end_ms).toBeUndefined();
    expect(validateForm(formOf({ silence_end_ms: 3000 }), "create").silence_end_ms).toBeUndefined();
  });
});
