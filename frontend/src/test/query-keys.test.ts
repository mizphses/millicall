import { describe, it, expect } from "vitest";

import {
  EXTENSIONS_KEY,
  TRUNKS_KEY,
  AGENTS_KEY,
  PROVIDERS_KEY,
  ROUTES_KEY,
  CONTACTS_KEY,
  CDR_KEY,
  CALL_MESSAGES_KEY,
  DASHBOARD_KEYS,
} from "../queryKeys";

/**
 * queryKeys.ts の衝突検証。
 * 一覧ページキー同士、および Dashboard キーと一覧ページキーが衝突しないことを保証する。
 */

describe("queryKeys", () => {
  const listKeys = [
    EXTENSIONS_KEY[0],
    TRUNKS_KEY[0],
    AGENTS_KEY[0],
    PROVIDERS_KEY[0],
    ROUTES_KEY[0],
    CONTACTS_KEY[0],
    CDR_KEY[0],
    CALL_MESSAGES_KEY[0],
  ] as const;

  it("一覧ページキーはすべて異なる（重複なし）", () => {
    const unique = new Set(listKeys);
    expect(unique.size).toBe(listKeys.length);
  });

  it("Dashboard キーのトップレベルは一覧ページキーと衝突しない", () => {
    const dashboardTopKeys = Object.values(DASHBOARD_KEYS).map((k) => k[0]);
    for (const dk of dashboardTopKeys) {
      expect(listKeys as readonly string[]).not.toContain(dk);
    }
  });

  it("Dashboard キー同士も重複しない", () => {
    const dashboardKeys = Object.values(DASHBOARD_KEYS).map((k) => k.join("/"));
    const unique = new Set(dashboardKeys);
    expect(unique.size).toBe(dashboardKeys.length);
  });
});
