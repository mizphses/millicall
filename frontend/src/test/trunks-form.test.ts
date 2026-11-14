import { describe, expect, it } from "vitest";

import { buildTrunkOptions } from "../pages/settings/trunkOptions";
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
  options_ping_seconds: 30,
  register_expire_seconds: 3600,
  fax_detect_enabled: false,
  fax_detect_timeout_sec: 20,
  fax_route_number: null,
  fax_detect_announce: false,
  dial_prefix: "",
  outbound_enabled: true,
  caller_id_mode: "as_dialed",
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
    options_ping_seconds: "30",
    register_expire_seconds: "3600",
    fax_detect_enabled: false,
    fax_detect_timeout_sec: "20",
    fax_route_number: "",
    fax_detect_announce: false,
    dial_prefix: "",
    outbound_enabled: true,
    caller_id_mode: "as_dialed",
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

  it("register_expire_seconds: 既定 3600、指定値を送る、空は 3600", () => {
    expect(buildCreatePayload(formOf({ password: "x" })).register_expire_seconds).toBe(3600);
    expect(buildCreatePayload(formOf({ register_expire_seconds: "60", password: "x" })).register_expire_seconds).toBe(60);
    expect(buildCreatePayload(formOf({ register_expire_seconds: "", password: "x" })).register_expire_seconds).toBe(3600);
  });

  it("options_ping_seconds: 既定 30、0 も送る、空は 30", () => {
    expect(buildCreatePayload(formOf({ password: "x" })).options_ping_seconds).toBe(30);
    expect(buildCreatePayload(formOf({ options_ping_seconds: "0", password: "x" })).options_ping_seconds).toBe(0);
    expect(buildCreatePayload(formOf({ options_ping_seconds: "", password: "x" })).options_ping_seconds).toBe(30);
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

  it("register_expire_seconds を変更したら含める / unchanged なら含めない", () => {
    expect(buildUpdatePayload(formOf({ register_expire_seconds: "120" }), original).register_expire_seconds).toBe(120);
    expect(buildUpdatePayload(formOf({ register_expire_seconds: "3600" }), original).register_expire_seconds).toBeUndefined();
  });

  it("options_ping_seconds を 0 に変更したら含める / unchanged なら含めない", () => {
    expect(buildUpdatePayload(formOf({ options_ping_seconds: "0" }), original).options_ping_seconds).toBe(0);
    expect(buildUpdatePayload(formOf({ options_ping_seconds: "30" }), original).options_ping_seconds).toBeUndefined();
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

describe("validateForm（登録更新間隔）", () => {
  it("範囲外・非整数を弾き、60〜3600 と空は許可", () => {
    expect(validateForm(formOf({ register_expire_seconds: "59" }), "edit").register_expire_seconds).toBeTruthy();
    expect(validateForm(formOf({ register_expire_seconds: "0" }), "edit").register_expire_seconds).toBeTruthy();
    expect(validateForm(formOf({ register_expire_seconds: "3601" }), "edit").register_expire_seconds).toBeTruthy();
    expect(validateForm(formOf({ register_expire_seconds: "90.5" }), "edit").register_expire_seconds).toBeTruthy();
    expect(validateForm(formOf({ register_expire_seconds: "60" }), "edit").register_expire_seconds).toBeUndefined();
    expect(validateForm(formOf({ register_expire_seconds: "3600" }), "edit").register_expire_seconds).toBeUndefined();
    expect(validateForm(formOf({ register_expire_seconds: "" }), "edit").register_expire_seconds).toBeUndefined();
  });
});

describe("validateForm（OPTIONS キープアライブ）", () => {
  it("範囲外・非整数を弾き、0〜3600 と空は許可", () => {
    expect(validateForm(formOf({ options_ping_seconds: "-1" }), "edit").options_ping_seconds).toBeTruthy();
    expect(validateForm(formOf({ options_ping_seconds: "3601" }), "edit").options_ping_seconds).toBeTruthy();
    expect(validateForm(formOf({ options_ping_seconds: "1.5" }), "edit").options_ping_seconds).toBeTruthy();
    expect(validateForm(formOf({ options_ping_seconds: "0" }), "edit").options_ping_seconds).toBeUndefined();
    expect(validateForm(formOf({ options_ping_seconds: "3600" }), "edit").options_ping_seconds).toBeUndefined();
    expect(validateForm(formOf({ options_ping_seconds: "" }), "edit").options_ping_seconds).toBeUndefined();
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

describe("FAX 自動検知", () => {
  it("未変更なら PATCH に含めない", () => {
    expect(buildUpdatePayload(formOf({}), original)).toEqual({});
  });

  it("有効化すると切替先・検知時間・案内音声を送る", () => {
    const payload = buildUpdatePayload(
      formOf({
        fax_detect_enabled: true,
        fax_route_number: "300",
        fax_detect_timeout_sec: "30",
        fax_detect_announce: true,
      }),
      original,
    );
    expect(payload).toEqual({
      fax_detect_enabled: true,
      fax_route_number: "300",
      fax_detect_timeout_sec: 30,
      fax_detect_announce: true,
    });
  });

  it("無効化すると切替先と案内音声もクリアする", () => {
    const enabled: TrunkRead = {
      ...original,
      fax_detect_enabled: true,
      fax_route_number: "300",
      fax_detect_announce: true,
    };
    const payload = buildUpdatePayload(
      formOf({ fax_detect_enabled: false, fax_route_number: "300", fax_detect_announce: true }),
      enabled,
    );
    expect(payload).toEqual({
      fax_detect_enabled: false,
      fax_route_number: null,
      fax_detect_announce: false,
    });
  });

  it("作成 payload は無効時に切替先を null にする", () => {
    const payload = buildCreatePayload(formOf({ fax_route_number: "300", password: "p" }));
    expect(payload.fax_detect_enabled).toBe(false);
    expect(payload.fax_route_number).toBeNull();
    expect(payload.fax_detect_timeout_sec).toBe(20);
  });

  it("有効なのに切替先が無いとエラー、検知時間は 5〜120", () => {
    expect(validateForm(formOf({ fax_detect_enabled: true }), "edit").fax_route_number).toBeTruthy();
    expect(
      validateForm(formOf({ fax_detect_enabled: true, fax_route_number: "300" }), "edit")
        .fax_route_number,
    ).toBeUndefined();
    expect(validateForm(formOf({ fax_detect_timeout_sec: "4" }), "edit").fax_detect_timeout_sec).toBeTruthy();
    expect(validateForm(formOf({ fax_detect_timeout_sec: "121" }), "edit").fax_detect_timeout_sec).toBeTruthy();
    expect(validateForm(formOf({ fax_detect_timeout_sec: "120" }), "edit").fax_detect_timeout_sec).toBeUndefined();
  });
});

describe("発信プレフィクス / 発信に使う（#139）", () => {
  it("作成 payload に dial_prefix（trim）と outbound_enabled を含める", () => {
    const payload = buildCreatePayload(formOf({ dial_prefix: " 92 ", outbound_enabled: false, password: "p" }));
    expect(payload.dial_prefix).toBe("92");
    expect(payload.outbound_enabled).toBe(false);
    expect(buildCreatePayload(formOf({ password: "p" })).dial_prefix).toBe("");
  });

  it("更新 payload は変更時のみ含め、空文字でプレフィクスなしに戻せる", () => {
    expect(buildUpdatePayload(formOf({ dial_prefix: "*3" }), original).dial_prefix).toBe("*3");
    expect(buildUpdatePayload(formOf({}), original).dial_prefix).toBeUndefined();
    expect(
      buildUpdatePayload(formOf({ dial_prefix: "" }), { ...original, dial_prefix: "92" }).dial_prefix,
    ).toBe("");
    expect(buildUpdatePayload(formOf({ outbound_enabled: false }), original).outbound_enabled).toBe(false);
    expect(buildUpdatePayload(formOf({}), original).outbound_enabled).toBeUndefined();
  });

  it("書式を検証する（0 始まり・1 始まり 3 桁・不正文字・9 文字以上は不可）", () => {
    for (const ok of ["", "9", "92", "*3", "#9", "12345678", "1234"]) {
      expect(validateForm(formOf({ dial_prefix: ok }), "edit").dial_prefix).toBeUndefined();
    }
    for (const bad of ["0", "09", "184", "186", "1860", "110", "9a", "9 2", "123456789"]) {
      expect(validateForm(formOf({ dial_prefix: bad }), "edit").dial_prefix).toBeTruthy();
    }
  });

  it("既定の発信トランクの選択肢は未設定 + 名前順で、使えないトランクを注記する", () => {
    const options = buildTrunkOptions([
      { id: 2, name: "b", display_name: "B", caller_id: "", enabled: true, outbound_enabled: false },
      { id: 1, name: "a", display_name: "A", caller_id: "0311111111", enabled: true, outbound_enabled: true },
      { id: 3, name: "c", display_name: "C", caller_id: "", enabled: false, outbound_enabled: true },
    ]);
    expect(options.map((o) => o.value)).toEqual(["", "1", "2", "3"]);
    expect(options[1].label).toBe("A（a / 番号通知 0311111111）");
    expect(options[2].label).toContain("着信専用");
    expect(options[3].label).toContain("無効");
  });
});

describe("番号通知（caller_id_mode, #139）", () => {
  it("作成 payload は既定 as_dialed、選んだモードを送る", () => {
    expect(buildCreatePayload(formOf({ password: "p" })).caller_id_mode).toBe("as_dialed");
    expect(buildCreatePayload(formOf({ caller_id_mode: "notify", password: "p" })).caller_id_mode).toBe("notify");
  });

  it("更新 payload は変更時のみ含める", () => {
    expect(buildUpdatePayload(formOf({ caller_id_mode: "withhold" }), original).caller_id_mode).toBe("withhold");
    expect(buildUpdatePayload(formOf({}), original).caller_id_mode).toBeUndefined();
  });
});
