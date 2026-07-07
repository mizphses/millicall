/**
 * ネットワーク設定ページのユニットテスト（Phase 5 Task 4）。
 *
 * DOM が不要なロジック（バリデーション・クエリキー衝突検証・フォームロジック）のみを
 * テストする。workflows-form.test.ts と同じスタイルに準拠する。
 */
import { describe, it, expect } from "vitest";

import {
  NETWORK_CONFIG_KEY,
  NETWORK_TAILSCALE_STATUS_KEY,
  EXTENSIONS_KEY,
  TRUNKS_KEY,
  AGENTS_KEY,
  PROVIDERS_KEY,
  ROUTES_KEY,
  CONTACTS_KEY,
  CDR_KEY,
  CALL_MESSAGES_KEY,
  WORKFLOWS_KEY,
  WORKFLOW_NODE_TYPES_KEY,
  DASHBOARD_KEYS,
} from "../queryKeys";

// ---------------------------------------------------------------------------
// クエリキー衝突テスト（Phase 5 Task 4 追加キーが既存キーと衝突しないこと）
// ---------------------------------------------------------------------------

describe("queryKeys — ネットワーク設定キーの衝突なし", () => {
  // 既存の全トップレベルキー
  const existingKeys = [
    EXTENSIONS_KEY[0],
    TRUNKS_KEY[0],
    AGENTS_KEY[0],
    PROVIDERS_KEY[0],
    ROUTES_KEY[0],
    CONTACTS_KEY[0],
    CDR_KEY[0],
    CALL_MESSAGES_KEY[0],
    WORKFLOWS_KEY[0],
    WORKFLOW_NODE_TYPES_KEY[0],
    ...Object.values(DASHBOARD_KEYS).map((k) => k[0]),
  ] as const;

  it("NETWORK_CONFIG_KEY は既存の全キーと衝突しない", () => {
    expect(existingKeys as readonly string[]).not.toContain(NETWORK_CONFIG_KEY[0]);
  });

  it("NETWORK_TAILSCALE_STATUS_KEY は既存の全キーと衝突しない", () => {
    expect(existingKeys as readonly string[]).not.toContain(NETWORK_TAILSCALE_STATUS_KEY[0]);
  });

  it("NETWORK_CONFIG_KEY と NETWORK_TAILSCALE_STATUS_KEY は互いに異なる", () => {
    expect(NETWORK_CONFIG_KEY[0]).not.toBe(NETWORK_TAILSCALE_STATUS_KEY[0]);
  });

  it("NETWORK_CONFIG_KEY は単一要素配列", () => {
    expect(NETWORK_CONFIG_KEY).toHaveLength(1);
  });

  it("NETWORK_TAILSCALE_STATUS_KEY は単一要素配列", () => {
    expect(NETWORK_TAILSCALE_STATUS_KEY).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Tailscale auth key フォームロジック
// ---------------------------------------------------------------------------

/**
 * NetworkPage の tailscale_auth_key フィールド送信ロジックを再現する。
 * "" → null（既存保持）、非空 → そのまま送信、null → null。
 */
function resolveTailscaleAuthKeyPayload(inputValue: string | null): string | null {
  if (inputValue === null || inputValue === "") {
    return null; // サーバーに null を送信 → 既存キー保持
  }
  return inputValue; // 非空 → サーバーへ送信して暗号化保存
}

describe("resolveTailscaleAuthKeyPayload — フォーム送信ロジック", () => {
  it("空文字列は null に変換される（既存キー保持）", () => {
    expect(resolveTailscaleAuthKeyPayload("")).toBeNull();
  });

  it("null は null のまま（既存キー保持）", () => {
    expect(resolveTailscaleAuthKeyPayload(null)).toBeNull();
  });

  it("有効なキー文字列はそのまま返す", () => {
    expect(resolveTailscaleAuthKeyPayload("tskey-abcdef12345")).toBe("tskey-abcdef12345");
  });

  it("空白のみの文字列は null に変換しない（バックエンド側でバリデーションエラーを期待）", () => {
    // 注: 空白文字列はバックエンドが is_valid_tailscale_authkey で弾く。
    // フロントエンドは変換せず、そのままサーバーに送ってエラーを受け取る。
    expect(resolveTailscaleAuthKeyPayload("   ")).toBe("   ");
  });
});

// ---------------------------------------------------------------------------
// Tailscale auth key の表示ロジック（書き込み専用）
// ---------------------------------------------------------------------------

/**
 * tailscale_auth_key_set に基づいて表示ラベルを決める。
 * auth key の値は絶対に表示しない。
 */
function authKeyDisplayLabel(keySet: boolean): string {
  return keySet ? "設定済み" : "未設定";
}

describe("authKeyDisplayLabel — 書き込み専用表示", () => {
  it("key が設定済みなら '設定済み'", () => {
    expect(authKeyDisplayLabel(true)).toBe("設定済み");
  });

  it("key が未設定なら '未設定'", () => {
    expect(authKeyDisplayLabel(false)).toBe("未設定");
  });

  it("表示文字列に 'tskey' や 'key' のような平文を含まない", () => {
    const label = authKeyDisplayLabel(true);
    expect(label).not.toContain("tskey");
    expect(label).not.toContain("key_value");
  });
});

// ---------------------------------------------------------------------------
// プロビジョニング URL 導出ロジック
// ---------------------------------------------------------------------------

/**
 * NetworkPage の apply フロー（バックエンドで実施されるが、フロントへの説明表示にも使う）。
 * provisioning_base_url が空の場合は lan_ip から URL を生成する。
 */
function deriveProvisioningUrl(provisioningBaseUrl: string, lanIp: string): string {
  if (!provisioningBaseUrl.trim()) {
    return `http://${lanIp}:8000/provisioning/`;
  }
  return provisioningBaseUrl;
}

describe("deriveProvisioningUrl — プレースホルダ説明ロジック", () => {
  it("空文字列なら lan_ip から URL を構築する", () => {
    expect(deriveProvisioningUrl("", "192.168.1.1")).toBe(
      "http://192.168.1.1:8000/provisioning/"
    );
  });

  it("空白文字列のみなら lan_ip から URL を構築する", () => {
    expect(deriveProvisioningUrl("   ", "10.0.0.1")).toBe("http://10.0.0.1:8000/provisioning/");
  });

  it("明示指定の URL はそのまま返す", () => {
    expect(deriveProvisioningUrl("http://custom.example/prov/", "192.168.1.1")).toBe(
      "http://custom.example/prov/"
    );
  });
});

// ---------------------------------------------------------------------------
// Tailscale ステータスの接続判定ロジック
// ---------------------------------------------------------------------------

/**
 * BackendState が "Running" のときのみ connected=true とする。
 * バックエンドの TailscaleStatusResult.connected の計算ロジックを再現する。
 */
function isTailscaleConnected(statusDetail: Record<string, unknown> | null | undefined): boolean {
  if (!statusDetail) return false;
  return statusDetail["BackendState"] === "Running";
}

describe("isTailscaleConnected — ステータス判定", () => {
  it("BackendState が 'Running' なら true", () => {
    expect(isTailscaleConnected({ BackendState: "Running" })).toBe(true);
  });

  it("BackendState が 'Stopped' なら false", () => {
    expect(isTailscaleConnected({ BackendState: "Stopped" })).toBe(false);
  });

  it("BackendState が 'NeedsLogin' なら false", () => {
    expect(isTailscaleConnected({ BackendState: "NeedsLogin" })).toBe(false);
  });

  it("null なら false", () => {
    expect(isTailscaleConnected(null)).toBe(false);
  });

  it("undefined なら false", () => {
    expect(isTailscaleConnected(undefined)).toBe(false);
  });

  it("空オブジェクトなら false", () => {
    expect(isTailscaleConnected({})).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// フォーム初期化ロジック（API レスポンスからフォーム状態への変換）
// ---------------------------------------------------------------------------

type MockNetworkConfig = {
  lan_interface: string;
  lan_ip: string;
  lan_prefix: number;
  dhcp_range_start: string;
  dhcp_range_end: string;
  dhcp_lease_hours: number;
  provisioning_base_url: string;
  nat_enabled: boolean;
  wan_interface: string;
  tailscale_enabled: boolean;
  tailscale_auth_key_set: boolean;
};

function configToLanDhcpForm(cfg: MockNetworkConfig) {
  return {
    lan_interface: cfg.lan_interface,
    lan_ip: cfg.lan_ip,
    lan_prefix: String(cfg.lan_prefix),
    dhcp_range_start: cfg.dhcp_range_start,
    dhcp_range_end: cfg.dhcp_range_end,
    dhcp_lease_hours: String(cfg.dhcp_lease_hours),
    provisioning_base_url: cfg.provisioning_base_url,
  };
}

const MOCK_CONFIG: MockNetworkConfig = {
  lan_interface: "eth0",
  lan_ip: "192.168.1.1",
  lan_prefix: 24,
  dhcp_range_start: "192.168.1.100",
  dhcp_range_end: "192.168.1.200",
  dhcp_lease_hours: 8,
  provisioning_base_url: "",
  nat_enabled: true,
  wan_interface: "eth1",
  tailscale_enabled: true,
  tailscale_auth_key_set: true,
};

describe("configToLanDhcpForm — API レスポンスからフォームへの変換", () => {
  it("lan_prefix を文字列に変換する", () => {
    const form = configToLanDhcpForm(MOCK_CONFIG);
    expect(typeof form.lan_prefix).toBe("string");
    expect(form.lan_prefix).toBe("24");
  });

  it("dhcp_lease_hours を文字列に変換する", () => {
    const form = configToLanDhcpForm(MOCK_CONFIG);
    expect(typeof form.dhcp_lease_hours).toBe("string");
    expect(form.dhcp_lease_hours).toBe("8");
  });

  it("tailscale_auth_key_set はフォームに含まれない（書き込み専用）", () => {
    const form = configToLanDhcpForm(MOCK_CONFIG);
    expect("tailscale_auth_key_set" in form).toBe(false);
  });

  it("tailscale_auth_key_encrypted はフォームに含まれない", () => {
    const form = configToLanDhcpForm(MOCK_CONFIG);
    expect("tailscale_auth_key_encrypted" in form).toBe(false);
  });

  it("lan_ip は変換なし（そのまま）", () => {
    const form = configToLanDhcpForm(MOCK_CONFIG);
    expect(form.lan_ip).toBe("192.168.1.1");
  });
});
