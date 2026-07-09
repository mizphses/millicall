/**
 * デバイス管理ページのユニットテスト（Phase 5 Task 6）。
 *
 * DOM が不要なロジック（バリデーション・クエリキー衝突検証・フォームロジック）のみを
 * テストする。workflows-form.test.ts / network-form.test.ts と同じスタイルに準拠する。
 */
import { describe, it, expect } from "vitest";

import {
  DEVICES_KEY,
  NETWORK_CONFIG_KEY,
  NETWORK_TAILSCALE_STATUS_KEY,
  EXTENSIONS_KEY,
  TRUNKS_KEY,
  AGENTS_KEY,
  PROVIDERS_KEY,
  NUMBER_PLAN_KEY,
  RING_GROUPS_KEY,
  CONTACTS_KEY,
  CDR_KEY,
  CALL_MESSAGES_KEY,
  WORKFLOWS_KEY,
  WORKFLOW_NODE_TYPES_KEY,
  DASHBOARD_KEYS,
} from "../queryKeys";

// ---------------------------------------------------------------------------
// クエリキー衝突テスト（Phase 5 Task 6 追加キーが既存キーと衝突しないこと）
// ---------------------------------------------------------------------------

describe("queryKeys — デバイス管理キーの衝突なし", () => {
  // 既存の全トップレベルキー（DEVICES_KEY を追加する前のもの）
  const existingKeys = [
    EXTENSIONS_KEY[0],
    TRUNKS_KEY[0],
    AGENTS_KEY[0],
    PROVIDERS_KEY[0],
    NUMBER_PLAN_KEY[0],
    RING_GROUPS_KEY[0],
    CONTACTS_KEY[0],
    CDR_KEY[0],
    CALL_MESSAGES_KEY[0],
    WORKFLOWS_KEY[0],
    WORKFLOW_NODE_TYPES_KEY[0],
    NETWORK_CONFIG_KEY[0],
    NETWORK_TAILSCALE_STATUS_KEY[0],
    ...Object.values(DASHBOARD_KEYS).map((k) => k[0]),
  ] as const;

  it("DEVICES_KEY は既存の全キーと衝突しない", () => {
    expect(existingKeys as readonly string[]).not.toContain(DEVICES_KEY[0]);
  });

  it("DEVICES_KEY は単一要素配列", () => {
    expect(DEVICES_KEY).toHaveLength(1);
  });

  it("DEVICES_KEY のトップレベル文字列は 'devices'", () => {
    expect(DEVICES_KEY[0]).toBe("devices");
  });

  it("DEVICES_KEY が既存の全ユニークトップレベルキーと重複しない", () => {
    // existingKeys に DASHBOARD_KEYS の重複エントリ（全て "dashboard"）が含まれるため
    // Set で重複排除してから確認する。
    const existingUnique = new Set(existingKeys);
    expect(existingUnique).not.toContain(DEVICES_KEY[0]);
    // DEVICES_KEY を加えた場合にサイズが 1 増えること（= 衝突なし）
    const withDevices = new Set([...existingUnique, DEVICES_KEY[0]]);
    expect(withDevices.size).toBe(existingUnique.size + 1);
  });
});

// ---------------------------------------------------------------------------
// quick-provision ペイロード構築ロジック
// ---------------------------------------------------------------------------

type QuickProvisionForm = {
  extension_number: string;
  display_name: string;
};

/**
 * DevicesPage の handleProvisionSubmit 前バリデーションを再現する。
 * 空欄チェックのみ（フォーマットチェックはバックエンドに委ねる）。
 */
function validateQuickProvisionForm(
  form: QuickProvisionForm
): Partial<Record<"extension_number" | "display_name", string>> {
  const errors: Partial<Record<"extension_number" | "display_name", string>> = {};
  if (!form.extension_number.trim()) {
    errors.extension_number = "内線番号を入力してください";
  }
  if (!form.display_name.trim()) {
    errors.display_name = "表示名を入力してください";
  }
  return errors;
}

describe("validateQuickProvisionForm — バリデーション", () => {
  it("両フィールド入力済みならエラーなし", () => {
    const errors = validateQuickProvisionForm({
      extension_number: "1001",
      display_name: "営業部 田中",
    });
    expect(Object.keys(errors)).toHaveLength(0);
  });

  it("extension_number が空ならエラー", () => {
    const errors = validateQuickProvisionForm({
      extension_number: "",
      display_name: "田中",
    });
    expect(errors.extension_number).toBeDefined();
    expect(errors.display_name).toBeUndefined();
  });

  it("display_name が空ならエラー", () => {
    const errors = validateQuickProvisionForm({
      extension_number: "1001",
      display_name: "",
    });
    expect(errors.extension_number).toBeUndefined();
    expect(errors.display_name).toBeDefined();
  });

  it("両フィールドが空なら 2 件のエラー", () => {
    const errors = validateQuickProvisionForm({
      extension_number: "",
      display_name: "",
    });
    expect(errors.extension_number).toBeDefined();
    expect(errors.display_name).toBeDefined();
  });

  it("空白のみの extension_number はエラー", () => {
    const errors = validateQuickProvisionForm({
      extension_number: "   ",
      display_name: "田中",
    });
    expect(errors.extension_number).toBeDefined();
  });

  it("空白のみの display_name はエラー", () => {
    const errors = validateQuickProvisionForm({
      extension_number: "1001",
      display_name: "   ",
    });
    expect(errors.display_name).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// quick-provision API ペイロード構築
// ---------------------------------------------------------------------------

/**
 * POST /api/devices/{id}/quick-provision に送るボディを構築する。
 * フロントエンドはトリム済み値を送る。
 */
function buildQuickProvisionBody(form: QuickProvisionForm): {
  extension_number: string;
  display_name: string;
} {
  return {
    extension_number: form.extension_number.trim(),
    display_name: form.display_name.trim(),
  };
}

describe("buildQuickProvisionBody — API ペイロード構築", () => {
  it("正常な入力はトリム済みで返す", () => {
    const body = buildQuickProvisionBody({ extension_number: "1001", display_name: "田中" });
    expect(body).toEqual({ extension_number: "1001", display_name: "田中" });
  });

  it("前後の空白はトリムされる", () => {
    const body = buildQuickProvisionBody({ extension_number: " 1002 ", display_name: " 佐藤 " });
    expect(body.extension_number).toBe("1002");
    expect(body.display_name).toBe("佐藤");
  });

  it("provision_token フィールドを含まない（セキュリティ）", () => {
    const body = buildQuickProvisionBody({ extension_number: "1001", display_name: "田中" });
    expect("provision_token" in body).toBe(false);
  });

  it("extension_number と display_name のみ含む", () => {
    const body = buildQuickProvisionBody({ extension_number: "2000", display_name: "ロビー" });
    expect(Object.keys(body)).toEqual(["extension_number", "display_name"]);
  });
});

// ---------------------------------------------------------------------------
// デバイス表示ロジック
// ---------------------------------------------------------------------------

/** 内線割当状態の表示ラベルを返す。 */
function extensionLabel(
  extensionNumber: string | null,
  extensionDisplayName: string | null
): string {
  if (!extensionNumber) return "未割当";
  if (extensionDisplayName) return `${extensionNumber} ${extensionDisplayName}`;
  return extensionNumber;
}

describe("extensionLabel — 内線表示ロジック", () => {
  it("extension_number が null なら '未割当'", () => {
    expect(extensionLabel(null, null)).toBe("未割当");
  });

  it("extension_number あり・display_name なし → 番号のみ", () => {
    expect(extensionLabel("1001", null)).toBe("1001");
  });

  it("extension_number あり・display_name あり → 番号 + 名前", () => {
    expect(extensionLabel("1001", "田中")).toBe("1001 田中");
  });
});

// ---------------------------------------------------------------------------
// last_seen 表示ロジック
// ---------------------------------------------------------------------------

/** last_seen が null なら '—' を返す。 */
function formatLastSeen(lastSeen: string | null): string {
  if (!lastSeen) return "—";
  return new Date(lastSeen).toLocaleString("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

describe("formatLastSeen — 最終確認日時フォーマット", () => {
  it("null なら '—'", () => {
    expect(formatLastSeen(null)).toBe("—");
  });

  it("有効な ISO 日時文字列は toLocaleString で変換される", () => {
    const result = formatLastSeen("2026-07-07T12:34:00Z");
    // ロケールによって文字列は変わるが、"—" でなければ OK
    expect(result).not.toBe("—");
    expect(typeof result).toBe("string");
  });
});

// ---------------------------------------------------------------------------
// sync レスポンス成功メッセージ構築
// ---------------------------------------------------------------------------

/** sync 完了時のトーストメッセージを構築する。 */
function buildSyncSuccessMessage(deviceCount: number): string {
  return `リース同期完了（${deviceCount} 台）`;
}

describe("buildSyncSuccessMessage — 同期完了メッセージ", () => {
  it("デバイス数を含むメッセージを返す", () => {
    expect(buildSyncSuccessMessage(5)).toBe("リース同期完了（5 台）");
  });

  it("0 台でも正常に動作する", () => {
    expect(buildSyncSuccessMessage(0)).toBe("リース同期完了（0 台）");
  });
});
