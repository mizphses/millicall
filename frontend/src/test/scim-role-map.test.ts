import { describe, expect, it } from "vitest";

import {
  parseRowsJson,
  roleMapToRowsJson,
  rowsJsonToRoleMap,
  SCIM_ROLE_OPTIONS,
} from "../pages/settings/scimRoleMap";

describe("roleMapToRowsJson", () => {
  it("map を行リスト JSON に変換する", () => {
    const json = roleMapToRowsJson({ "millicall-admins": "admin", staff: "user" });
    expect(JSON.parse(json)).toEqual([
      { group: "millicall-admins", role: "admin" },
      { group: "staff", role: "user" },
    ]);
  });

  it("map 以外の値（null / 配列 / undefined）は空リストになる", () => {
    expect(JSON.parse(roleMapToRowsJson(null))).toEqual([]);
    expect(JSON.parse(roleMapToRowsJson(undefined))).toEqual([]);
    expect(JSON.parse(roleMapToRowsJson(["a"]))).toEqual([]);
  });

  it("ロールが文字列でない値は user にフォールバックする", () => {
    expect(JSON.parse(roleMapToRowsJson({ g: 1 }))).toEqual([{ group: "g", role: "user" }]);
  });
});

describe("parseRowsJson", () => {
  it("不正な JSON・非配列は空リストにフォールバックする", () => {
    expect(parseRowsJson("not-json")).toEqual([]);
    expect(parseRowsJson('{"a":1}')).toEqual([]);
    expect(parseRowsJson("")).toEqual([]);
    expect(parseRowsJson(true)).toEqual([]);
  });

  it("欠損フィールドを補完する", () => {
    expect(parseRowsJson('[{"group":"g"},{}]')).toEqual([
      { group: "g", role: "user" },
      { group: "", role: "user" },
    ]);
  });
});

describe("rowsJsonToRoleMap", () => {
  it("行リストを map に変換し、グループ名を trim する", () => {
    const raw = JSON.stringify([
      { group: "  millicall-admins ", role: "admin" },
      { group: "staff", role: "user" },
    ]);
    expect(rowsJsonToRoleMap(raw)).toEqual({ "millicall-admins": "admin", staff: "user" });
  });

  it("グループ名が空の編集途中の行は除外する", () => {
    const raw = JSON.stringify([
      { group: "g", role: "admin" },
      { group: "  ", role: "user" },
    ]);
    expect(rowsJsonToRoleMap(raw)).toEqual({ g: "admin" });
  });

  it("全行を消すと空 map（自動付与オフ）になる", () => {
    expect(rowsJsonToRoleMap("[]")).toEqual({});
  });

  it("グループ名の重複は Error", () => {
    const raw = JSON.stringify([
      { group: "g", role: "admin" },
      { group: " g ", role: "user" },
    ]);
    expect(() => rowsJsonToRoleMap(raw)).toThrow(/重複/);
  });
});

describe("SCIM_ROLE_OPTIONS", () => {
  it("バックエンドの許可ロールと一致する", () => {
    expect([...SCIM_ROLE_OPTIONS]).toEqual(["user", "admin"]);
  });
});
