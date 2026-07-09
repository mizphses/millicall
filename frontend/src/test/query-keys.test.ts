import { describe, it, expect } from "vitest";

import {
  EXTENSIONS_KEY,
  TRUNKS_KEY,
  AGENTS_KEY,
  PROVIDERS_KEY,
  NUMBER_PLAN_KEY,
  RING_GROUPS_KEY,
  CONTACTS_KEY,
  CDR_KEY,
  CALL_MESSAGES_KEY,
  DASHBOARD_KEYS,
  WORKFLOWS_KEY,
  NETWORK_CONFIG_KEY,
  DEVICES_KEY,
  USERS_KEY,
  SYSTEM_CONTAINERS_KEY,
  SYSTEM_INFO_KEY,
  AUTH_ME_KEY,
  auditKey,
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
    NUMBER_PLAN_KEY[0],
    RING_GROUPS_KEY[0],
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

  it("Phase 6 キーのトップレベルは既存キーと衝突しない", () => {
    const existingTop = [
      ...listKeys,
      WORKFLOWS_KEY[0],
      NETWORK_CONFIG_KEY[0],
      DEVICES_KEY[0],
      ...Object.values(DASHBOARD_KEYS).map((k) => k[0]),
    ] as readonly string[];
    const p6Top = [
      USERS_KEY[0],
      SYSTEM_CONTAINERS_KEY[0],
      SYSTEM_INFO_KEY[0],
      AUTH_ME_KEY[0],
      auditKey(50, 0)[0],
    ];
    for (const k of p6Top) {
      expect(existingTop).not.toContain(k);
    }
    // Phase 6 トップレベル同士も一意
    expect(new Set(p6Top).size).toBe(p6Top.length);
  });

  it("auditKey は limit/offset を含む一意なキーを返す", () => {
    expect(auditKey(50, 0)).toEqual(["p6-audit", 50, 0]);
    expect(auditKey(50, 50)).toEqual(["p6-audit", 50, 50]);
    expect(auditKey(50, 0)).not.toEqual(auditKey(50, 50));
  });
});
