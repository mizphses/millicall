/**
 * デバイスの配信状態判定と resync トースト文言のユニットテスト（issue #85）。
 */
import { describe, it, expect } from "vitest";

import {
  deviceStatus,
  resyncToast,
  type DeviceStatusInput,
} from "../pages/devices/deviceStatus";

function dev(overrides: Partial<DeviceStatusInput> = {}): DeviceStatusInput {
  return {
    id: 1,
    provisioned: true,
    provision_mode: "standard",
    token_issued: false,
    last_config_fetch_at: null,
    last_config_fetch_used_token: false,
    ...overrides,
  };
}

const REGISTERED = { available: true, registered_device_ids: [1] };
const NOT_REGISTERED = { available: true, registered_device_ids: [] };
const UNAVAILABLE = { available: false, registered_device_ids: [] };
const FETCHED = "2026-11-10T00:00:00";

describe("deviceStatus — 完了は取得済みかつ登録確認済みのときだけ", () => {
  it("未割当", () => {
    expect(deviceStatus(dev({ provisioned: false }), REGISTERED).kind).toBe("unassigned");
  });

  it("standard: 未取得は取得待ち（登録済みでも完了にしない）", () => {
    expect(deviceStatus(dev(), REGISTERED).kind).toBe("fetch_waiting");
  });

  it("standard: 取得済み + 登録済み → 完了", () => {
    const s = deviceStatus(dev({ last_config_fetch_at: FETCHED }), REGISTERED);
    expect(s.kind).toBe("complete");
    expect(s.label).toBe("完了");
  });

  it("取得済みでも未登録なら完了にしない", () => {
    expect(deviceStatus(dev({ last_config_fetch_at: FETCHED }), NOT_REGISTERED).kind).toBe(
      "fetched_unregistered"
    );
  });

  it("登録状態が取れない（FreeSWITCH 不通・未取得）なら登録未確認", () => {
    expect(deviceStatus(dev({ last_config_fetch_at: FETCHED }), UNAVAILABLE).kind).toBe(
      "fetched_unverified"
    );
    expect(deviceStatus(dev({ last_config_fetch_at: FETCHED }), null).kind).toBe(
      "fetched_unverified"
    );
  });

  it("strict: トークン未発行", () => {
    expect(deviceStatus(dev({ provision_mode: "strict" }), REGISTERED).kind).toBe(
      "token_not_issued"
    );
  });

  it("strict: トークン発行済み・トークンなしの過去取得はトークン待ち", () => {
    const s = deviceStatus(
      dev({ provision_mode: "strict", token_issued: true, last_config_fetch_at: FETCHED }),
      REGISTERED
    );
    expect(s.kind).toBe("token_waiting");
  });

  it("strict: トークン付き取得済み + 登録済み → 完了", () => {
    const s = deviceStatus(
      dev({
        provision_mode: "strict",
        token_issued: true,
        last_config_fetch_at: FETCHED,
        last_config_fetch_used_token: true,
      }),
      REGISTERED
    );
    expect(s.kind).toBe("complete");
  });
});

describe("resyncToast", () => {
  it("受理された手段を列挙し、未検証の手段を明示する", () => {
    const t = resyncToast({
      requested: true,
      attempts: [
        { method: "sip_notify_check_sync", ok: true, detail: "", verified: true, skipped: false },
        { method: "panasonic_cgi_provision", ok: true, detail: "HTTP 200", verified: false, skipped: false },
      ],
    });
    expect(t.tone).toBe("success");
    expect(t.message).toContain("SIP NOTIFY");
    expect(t.message).toContain("Panasonic CGI（未検証）");
    expect(t.message).toContain("状態列で確認");
  });

  it("失敗時は手段ごとの理由を出す", () => {
    const t = resyncToast({
      requested: false,
      attempts: [
        { method: "yealink_action_uri", ok: false, detail: "HTTP 302", verified: true, skipped: false },
      ],
    });
    expect(t.tone).toBe("danger");
    expect(t.message).toContain("Yealink Action URI: HTTP 302");
  });
});
