import { describe, expect, it } from "vitest";

import {
  buildCreatePayload,
  buildUpdatePayload,
  validateForm,
  type RouteFormValues,
  type RouteRead,
} from "../pages/routes/formPayload";

const originalExtension: RouteRead = {
  id: 1,
  match_number: "0312345678",
  target_type: "extension",
  target_value: "1001",
  enabled: true,
};

const originalAgent: RouteRead = {
  id: 2,
  match_number: "0398765432",
  target_type: "ai_agent",
  target_value: "5",
  enabled: true,
};

function formOf(overrides: Partial<RouteFormValues>): RouteFormValues {
  return {
    match_number: "0312345678",
    target_type: "extension",
    target_value: "1001",
    enabled: true,
    ...overrides,
  };
}

describe("buildCreatePayload", () => {
  it("extension: target_value は内線番号（文字列）そのまま", () => {
    const payload = buildCreatePayload(
      formOf({ target_type: "extension", target_value: "1001" }),
    );
    expect(payload.target_type).toBe("extension");
    expect(payload.target_value).toBe("1001");
    expect(typeof payload.target_value).toBe("string");
  });

  it("ai_agent: target_value はエージェント id を文字列化したもの", () => {
    const payload = buildCreatePayload(
      formOf({ target_type: "ai_agent", target_value: "5" }),
    );
    expect(payload.target_type).toBe("ai_agent");
    expect(payload.target_value).toBe("5");
    expect(typeof payload.target_value).toBe("string");
  });

  it("match_number を trim する", () => {
    const payload = buildCreatePayload(formOf({ match_number: " 090 " }));
    expect(payload.match_number).toBe("090");
  });

  it("enabled フラグを含む", () => {
    const payload = buildCreatePayload(formOf({ enabled: false }));
    expect(payload.enabled).toBe(false);
  });
});

describe("buildUpdatePayload（編集フォーム → PATCH payload 変換）", () => {
  it("変更がなければ空 payload", () => {
    expect(buildUpdatePayload(formOf({}), originalExtension)).toEqual({});
  });

  it("target_value が変わったら target_type と target_value を両方含める", () => {
    const payload = buildUpdatePayload(
      formOf({ target_type: "extension", target_value: "1002" }),
      originalExtension,
    );
    expect(payload.target_type).toBe("extension");
    expect(payload.target_value).toBe("1002");
  });

  it("target_type が変わったら target_type と target_value を両方含める", () => {
    const payload = buildUpdatePayload(
      formOf({ target_type: "ai_agent", target_value: "3" }),
      originalExtension,
    );
    expect(payload.target_type).toBe("ai_agent");
    expect(payload.target_value).toBe("3");
  });

  it("ai_agent → target_value はエージェント id 文字列", () => {
    const payload = buildUpdatePayload(
      formOf({ target_type: "ai_agent", target_value: "7" }),
      originalAgent,
    );
    expect(payload.target_value).toBe("7");
  });

  it("enabled を切り替えたら含める", () => {
    const payload = buildUpdatePayload(formOf({ enabled: false }), originalExtension);
    expect(payload.enabled).toBe(false);
  });

  it("target と enabled を同時に変更したら全て含める", () => {
    const payload = buildUpdatePayload(
      formOf({ target_type: "ai_agent", target_value: "2", enabled: false }),
      originalExtension,
    );
    expect(payload.target_type).toBe("ai_agent");
    expect(payload.target_value).toBe("2");
    expect(payload.enabled).toBe(false);
  });
});

describe("validateForm（作成モード）", () => {
  it("マッチ番号パターン不正を弾く", () => {
    expect(validateForm(formOf({ match_number: "abc" }), "create").match_number).toBeTruthy();
    expect(validateForm(formOf({ match_number: "" }), "create").match_number).toBeTruthy();
  });

  it("正しいマッチ番号はエラーなし", () => {
    expect(validateForm(formOf({ match_number: "0312345678" }), "create").match_number).toBeUndefined();
    expect(validateForm(formOf({ match_number: "*" }), "create").match_number).toBeUndefined();
    expect(validateForm(formOf({ match_number: "#100" }), "create").match_number).toBeUndefined();
  });

  it("target_value が空なら弾く", () => {
    expect(validateForm(formOf({ target_value: "" }), "create").target_value).toBeTruthy();
  });

  it("target_value が非空ならエラーなし", () => {
    expect(validateForm(formOf({ target_value: "1001" }), "create").target_value).toBeUndefined();
  });
});

describe("validateForm（編集モード）", () => {
  it("match_number は検証しない（編集不可）", () => {
    expect(validateForm(formOf({ match_number: "" }), "edit").match_number).toBeUndefined();
  });

  it("target_value が空なら弾く（編集時も転送先は必須）", () => {
    expect(validateForm(formOf({ target_value: "" }), "edit").target_value).toBeTruthy();
  });
});
