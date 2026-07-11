import { describe, expect, it } from "vitest";

import { NAV_ITEMS, NAV_SECTIONS, activeNavPath, titleForPath } from "../shell/nav";

/**
 * nav.ts のセクション構造テスト。
 * カテゴリ化後も全パスがフラットに列挙され、titleForPath が引けることを保証する。
 */

describe("NAV_SECTIONS", () => {
  it("先頭セクションは見出しなし（ダッシュボードのみ）", () => {
    expect(NAV_SECTIONS[0].title).toBeNull();
    expect(NAV_SECTIONS[0].items.map((i) => i.path)).toEqual(["/"]);
  });

  it("カテゴリの見出しと順序が定義どおり", () => {
    expect(NAV_SECTIONS.map((s) => s.title)).toEqual([null, "回線", "設定", "監査"]);
  });

  it("NAV_ITEMS は全セクションのフラット列挙と一致する", () => {
    expect(NAV_ITEMS).toEqual(NAV_SECTIONS.flatMap((s) => s.items));
  });

  it("全パスが漏れなく列挙される", () => {
    const paths = NAV_ITEMS.map((i) => i.path);
    expect(paths).toEqual([
      "/",
      "/extensions",
      "/trunks",
      "/routes",
      "/ai-agents",
      "/workflows",
      "/contacts",
      "/providers",
      "/devices",
      "/network",
      "/network/remote",
      "/users",
      "/system",
      "/sso",
      "/settings/security",
      "/settings",
      "/cdr",
      "/audit",
    ]);
  });

  it("パスに重複がない", () => {
    const paths = NAV_ITEMS.map((i) => i.path);
    expect(new Set(paths).size).toBe(paths.length);
  });
});

describe("titleForPath", () => {
  it("完全一致で画面題を引ける", () => {
    expect(titleForPath("/")).toBe("ダッシュボード");
    expect(titleForPath("/extensions")).toBe("内線");
    expect(titleForPath("/routes")).toBe("ルーティング");
    expect(titleForPath("/network")).toBe("ネットワーク（内向き）");
    expect(titleForPath("/network/remote")).toBe("ネットワーク（外向き）");
    expect(titleForPath("/settings/security")).toBe("セキュリティ");
    expect(titleForPath("/audit")).toBe("監査ログ");
  });

  it("全ナビ項目のパスが引ける（フラット探索）", () => {
    for (const item of NAV_ITEMS) {
      expect(titleForPath(item.path)).toBe(item.label);
    }
  });

  it("プレフィックス一致でも引ける（詳細画面など）", () => {
    expect(titleForPath("/workflows/5")).toBe("ワークフロー");
  });

  it("未知のパスはフォールバック名を返す", () => {
    expect(titleForPath("/unknown")).toBe("millicall");
  });
});

describe("activeNavPath", () => {
  it("完全一致のパスを返す", () => {
    expect(activeNavPath("/")).toBe("/");
    expect(activeNavPath("/extensions")).toBe("/extensions");
  });

  it("入れ子パスでは最長一致の 1 件だけを返す", () => {
    expect(activeNavPath("/network")).toBe("/network");
    expect(activeNavPath("/network/remote")).toBe("/network/remote");
  });

  it("詳細画面はセグメント境界のプレフィックスで一致する", () => {
    expect(activeNavPath("/workflows/5")).toBe("/workflows");
  });

  it("未知のパスは null を返す", () => {
    expect(activeNavPath("/unknown")).toBeNull();
  });
});
