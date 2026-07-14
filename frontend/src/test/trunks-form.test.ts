import { describe, expect, it } from "vitest";

import {
  buildCreatePayload,
  buildUpdatePayload,
  validateForm,
  type TrunkFormValues,
  type TrunkRead,
} from "../pages/trunks/formPayload";

const original: TrunkRead = {
  id: 1,
  name: "my-trunk",
  display_name: "本社回線",
  host: "sip.example.com",
  username: "trunk-user",
  did_number: "0312345678",
  caller_id: "0312345678",
  inbound_extension: "200",
  source_port: null,
  trunk_type: "hgw",
  inbound_cidrs: [],
  enabled: true,
  has_password: true,
};

function formOf(overrides: Partial<TrunkFormValues>): TrunkFormValues {
  return {
    name: "my-trunk",
    display_name: "本社回線",
    host: "sip.example.com",
    username: "trunk-user",
    password: "",
    did_number: "0312345678",
    caller_id: "0312345678",
    inbound_extension: "200",
    source_port: "",
    trunk_type: "hgw",
    inbound_cidrs: "",
    enabled: true,
    ...overrides,
  };
}

describe("buildCreatePayload", () => {
  it("全フィールドを含み、文字列フィールドは trim する", () => {
    const payload = buildCreatePayload(
      formOf({ name: " my-trunk ", display_name: " 本社 ", host: " sip.example.com ", password: "secret123" }),
    );
    expect(payload.name).toBe("my-trunk");
    expect(payload.display_name).toBe("本社");
    expect(payload.host).toBe("sip.example.com");
    expect(payload.password).toBe("secret123");
    expect(payload.enabled).toBe(true);
  });

  it("did_number・caller_id は省略可能（空文字でも送る）", () => {
    const payload = buildCreatePayload(formOf({ did_number: "", caller_id: "", password: "secret" }));
    expect(payload.did_number).toBe("");
    expect(payload.caller_id).toBe("");
  });

  it("inbound_extension を含める（trim あり・空文字 = 着信しない も送る）", () => {
    expect(buildCreatePayload(formOf({ inbound_extension: " 200 ", password: "x" })).inbound_extension).toBe("200");
    expect(buildCreatePayload(formOf({ inbound_extension: "", password: "x" })).inbound_extension).toBe("");
  });

  it("source_port: 空文字 = null（自動採番）、数値文字列 = number", () => {
    expect(buildCreatePayload(formOf({ source_port: "", password: "x" })).source_port).toBeNull();
    expect(buildCreatePayload(formOf({ source_port: "5082", password: "x" })).source_port).toBe(5082);
  });

  it("trunk_type と inbound_cidrs を含める（改行/カンマ区切りを配列へ）", () => {
    const payload = buildCreatePayload(
      formOf({
        trunk_type: "sip",
        inbound_cidrs: "203.0.113.0/24\n198.51.100.7, 192.0.2.0/24",
        password: "x",
      }),
    );
    expect(payload.trunk_type).toBe("sip");
    expect(payload.inbound_cidrs).toEqual(["203.0.113.0/24", "198.51.100.7", "192.0.2.0/24"]);
  });

  it("既定は hgw / inbound_cidrs は空配列", () => {
    const payload = buildCreatePayload(formOf({ password: "x" }));
    expect(payload.trunk_type).toBe("hgw");
    expect(payload.inbound_cidrs).toEqual([]);
  });
});

describe("buildUpdatePayload（編集フォーム → PATCH payload 変換）", () => {
  it("変更がなければ空 payload（password も空なので含めない）", () => {
    expect(buildUpdatePayload(formOf({}), original)).toEqual({});
  });

  it("password が空なら含めない（omit-if-empty: 据え置き）", () => {
    const payload = buildUpdatePayload(formOf({ password: "" }), original);
    expect(payload.password).toBeUndefined();
  });

  it("password が非空なら含める（original との比較なし = write-only）", () => {
    const payload = buildUpdatePayload(formOf({ password: "new-secret" }), original);
    expect(payload.password).toBe("new-secret");
  });

  it("display_name が空文字なら含めない（据え置き）", () => {
    const payload = buildUpdatePayload(formOf({ display_name: "   " }), original);
    expect(payload.display_name).toBeUndefined();
  });

  it("display_name が変更されたら含める", () => {
    const payload = buildUpdatePayload(formOf({ display_name: "新名称" }), original);
    expect(payload.display_name).toBe("新名称");
  });

  it("host が変更されたら含める", () => {
    const payload = buildUpdatePayload(formOf({ host: "sip.new.example.com" }), original);
    expect(payload.host).toBe("sip.new.example.com");
  });

  it("username が変更されたら含める", () => {
    const payload = buildUpdatePayload(formOf({ username: "new-user" }), original);
    expect(payload.username).toBe("new-user");
  });

  it("did_number が変更されたら含める（空文字への変更も含める）", () => {
    const payload = buildUpdatePayload(formOf({ did_number: "" }), original);
    expect(payload.did_number).toBe("");
  });

  it("caller_id が変更されたら含める（空文字への変更も含める）", () => {
    const payload = buildUpdatePayload(formOf({ caller_id: "" }), original);
    expect(payload.caller_id).toBe("");
  });

  it("inbound_extension が変更されたら含める", () => {
    const payload = buildUpdatePayload(formOf({ inbound_extension: "300" }), original);
    expect(payload.inbound_extension).toBe("300");
  });

  it("inbound_extension を空文字（着信しない）に変更したら含める", () => {
    const payload = buildUpdatePayload(formOf({ inbound_extension: "" }), original);
    expect(payload.inbound_extension).toBe("");
  });

  it("inbound_extension が unchanged なら含めない", () => {
    const payload = buildUpdatePayload(formOf({ inbound_extension: "200" }), original);
    expect(payload.inbound_extension).toBeUndefined();
  });

  it("enabled を切り替えたら含める", () => {
    const payload = buildUpdatePayload(formOf({ enabled: false }), original);
    expect(payload.enabled).toBe(false);
  });

  it("source_port を明示指定したら含める（number）", () => {
    const payload = buildUpdatePayload(formOf({ source_port: "5090" }), original);
    expect(payload.source_port).toBe(5090);
  });

  it("source_port が unchanged（空 = null のまま）なら含めない", () => {
    const payload = buildUpdatePayload(formOf({ source_port: "" }), original);
    expect(payload.source_port).toBeUndefined();
  });

  it("source_port を明示から自動採番（空 = null）へ戻したら null を含める", () => {
    const withPort: TrunkRead = { ...original, source_port: 5082 };
    const payload = buildUpdatePayload(formOf({ source_port: "" }), withPort);
    expect("source_port" in payload).toBe(true);
    expect(payload.source_port).toBeNull();
  });

  it("trunk_type を変更したら含める", () => {
    const payload = buildUpdatePayload(formOf({ trunk_type: "sip" }), original);
    expect(payload.trunk_type).toBe("sip");
  });

  it("inbound_cidrs を変更したら配列で含める / unchanged なら含めない", () => {
    const changed = buildUpdatePayload(
      formOf({ trunk_type: "sip", inbound_cidrs: "203.0.113.0/24" }),
      original,
    );
    expect(changed.inbound_cidrs).toEqual(["203.0.113.0/24"]);

    const same: TrunkRead = { ...original, inbound_cidrs: ["203.0.113.0/24"] };
    const unchanged = buildUpdatePayload(
      formOf({ inbound_cidrs: "203.0.113.0/24" }),
      same,
    );
    expect(unchanged.inbound_cidrs).toBeUndefined();
  });

  it("複数フィールドを同時に変更したら全て含める", () => {
    const payload = buildUpdatePayload(
      formOf({ display_name: "別名称", password: "changed", enabled: false }),
      original,
    );
    expect(payload.display_name).toBe("別名称");
    expect(payload.password).toBe("changed");
    expect(payload.enabled).toBe(false);
  });
});

describe("validateForm（作成モード）", () => {
  it("名前パターン不正を弾く", () => {
    expect(validateForm(formOf({ name: "invalid name!", password: "x" }), "create").name).toBeTruthy();
    expect(validateForm(formOf({ name: "" }), "create").name).toBeTruthy();
  });

  it("正しい名前はエラーなし", () => {
    expect(validateForm(formOf({ name: "valid-trunk_1", password: "x" }), "create").name).toBeUndefined();
  });

  it("display_name が空なら弾く", () => {
    expect(validateForm(formOf({ display_name: "" }), "create").display_name).toBeTruthy();
  });

  it("host が空なら弾く", () => {
    expect(validateForm(formOf({ host: "" }), "create").host).toBeTruthy();
  });

  it("username が空なら弾く", () => {
    expect(validateForm(formOf({ username: "" }), "create").username).toBeTruthy();
  });

  it("password が空なら弾く（作成時は必須）", () => {
    expect(validateForm(formOf({ password: "" }), "create").password).toBeTruthy();
  });

  it("password が非空ならエラーなし", () => {
    expect(validateForm(formOf({ password: "secret" }), "create").password).toBeUndefined();
  });
});

describe("validateForm（編集モード）", () => {
  it("全フィールド空でもエラーなし（据え置き扱い）", () => {
    const errors = validateForm(
      formOf({ display_name: "", host: "", username: "", password: "" }),
      "edit",
    );
    expect(Object.keys(errors)).toHaveLength(0);
  });

  it("did_number が 31 文字以上なら弾く", () => {
    expect(validateForm(formOf({ did_number: "0".repeat(31) }), "create").did_number).toBeTruthy();
  });

  it("source_port が範囲外なら弾く / 範囲内・空はエラーなし", () => {
    expect(validateForm(formOf({ source_port: "80" }), "edit").source_port).toBeTruthy();
    expect(validateForm(formOf({ source_port: "70000" }), "edit").source_port).toBeTruthy();
    expect(validateForm(formOf({ source_port: "5082" }), "edit").source_port).toBeUndefined();
    expect(validateForm(formOf({ source_port: "" }), "edit").source_port).toBeUndefined();
  });
});

describe("validateForm（着信許可 CIDR）", () => {
  it("SIP 種別で不正な CIDR を弾く", () => {
    const errors = validateForm(
      formOf({ trunk_type: "sip", inbound_cidrs: "203.0.113.0/24\nnot-an-ip", password: "x" }),
      "create",
    );
    expect(errors.inbound_cidrs).toBeTruthy();
  });

  it("SIP 種別で正しい CIDR/アドレスはエラーなし", () => {
    const errors = validateForm(
      formOf({ trunk_type: "sip", inbound_cidrs: "203.0.113.0/24\n198.51.100.7", password: "x" }),
      "create",
    );
    expect(errors.inbound_cidrs).toBeUndefined();
  });

  it("HGW 種別では CIDR 検証しない（不正でも無視）", () => {
    const errors = validateForm(
      formOf({ trunk_type: "hgw", inbound_cidrs: "garbage", password: "x" }),
      "create",
    );
    expect(errors.inbound_cidrs).toBeUndefined();
  });
});
