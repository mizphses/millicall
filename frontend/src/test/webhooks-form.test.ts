import { describe, expect, it } from "vitest";

import {
  NUMBER_PLAN_KIND_LABELS,
  buildFaxReceiverPayload,
  numberPlanKindLabel,
  validateFaxReceiverForm,
} from "../pages/routes/formPayload";
import {
  buildCreatePayload,
  buildUpdatePayload,
  deliveryStatus,
  emptyWebhookForm,
  formFromWebhook,
  WEBHOOK_FORMAT_OPTIONS,
  isChatFormat,
  toggleEvent,
  validateWebhookForm,
  webhookEventLabel,
  webhookFormatLabel,
  webhookFormatOption,
  willSendUnsigned,
  type WebhookRead,
} from "../pages/webhooks/formPayload";

const original: WebhookRead = {
  id: 1,
  name: "CRM",
  url: "https://crm.example.com/hook",
  format: "generic",
  events: ["fax.received", "call.inbound"],
  enabled: true,
  has_secret: true,
  created_at: "2026-11-14T00:00:00",
};

describe("webhook フォーム状態", () => {
  it("新規は全イベント選択・有効で始まる", () => {
    const form = emptyWebhookForm();
    expect(form.events).toEqual(["call.inbound", "call.ended", "fax.received"]);
    expect(form.enabled).toBe(true);
    expect(form.secret).toBe("");
  });

  it("編集フォームはシークレットを持たず、イベントを表示順に並べる", () => {
    const form = formFromWebhook(original);
    expect(form.secret).toBe("");
    expect(form.events).toEqual(["call.inbound", "fax.received"]);
  });

  it("toggleEvent は表示順を保って追加・削除する", () => {
    expect(toggleEvent(["fax.received"], "call.inbound", true)).toEqual([
      "call.inbound",
      "fax.received",
    ]);
    expect(toggleEvent(["call.inbound", "fax.received"], "call.inbound", false)).toEqual([
      "fax.received",
    ]);
  });

  it("作成 payload は trim し、シークレットを含める", () => {
    const payload = buildCreatePayload({
      ...emptyWebhookForm(),
      name: " CRM ",
      url: " https://a.example/h ",
      secret: "k",
    });
    expect(payload).toMatchObject({ name: "CRM", url: "https://a.example/h", secret: "k" });
  });

  it("更新 payload: 未変更は空、シークレット空欄は据え置き、削除指定は空文字", () => {
    const form = formFromWebhook(original);
    expect(buildUpdatePayload(form, original)).toEqual({});
    expect(buildUpdatePayload({ ...form, secret: "new" }, original)).toEqual({ secret: "new" });
    expect(buildUpdatePayload({ ...form, clear_secret: true }, original)).toEqual({ secret: "" });
    expect(
      buildUpdatePayload({ ...form, events: ["call.ended"], enabled: false }, original),
    ).toEqual({ events: ["call.ended"], enabled: false });
  });

  it("バリデーション: URL スキーム・認証情報・イベント必須", () => {
    const base = { ...emptyWebhookForm(), name: "x", url: "https://a.example/" };
    expect(validateWebhookForm(base)).toEqual({});
    expect(validateWebhookForm({ ...base, url: "ftp://a.example/" }).url).toBeTruthy();
    expect(validateWebhookForm({ ...base, url: "javascript:alert(1)" }).url).toBeTruthy();
    expect(validateWebhookForm({ ...base, url: "https://u:p@a.example/" }).url).toBeTruthy();
    expect(validateWebhookForm({ ...base, events: [] }).events).toBeTruthy();
    expect(validateWebhookForm({ ...base, name: " " }).name).toBeTruthy();
  });

  it("配信ログとイベントの表示", () => {
    expect(deliveryStatus({ success: true, status_code: 200, error: null }).tone).toBe("success");
    expect(deliveryStatus({ success: false, status_code: 500, error: "HTTP 500" }).label).toBe(
      "失敗（HTTP 500）",
    );
    expect(deliveryStatus({ success: false, status_code: null, error: "x" }).label).toBe(
      "失敗（接続エラー）",
    );
    expect(webhookEventLabel("webhook.test")).toBe("テスト送信");
    expect(webhookEventLabel("fax.received")).toBe("FAX 受信");
  });
});

describe("署名なし送信の警告", () => {
  it("新規でシークレット空、または削除指定なら警告する", () => {
    const g = "generic" as const;
    expect(willSendUnsigned({ format: g, secret: "", clear_secret: false }, null)).toBe(true);
    expect(willSendUnsigned({ format: g, secret: "k", clear_secret: false }, null)).toBe(false);
    expect(
      willSendUnsigned({ format: g, secret: "", clear_secret: false }, { has_secret: true }),
    ).toBe(false);
    expect(
      willSendUnsigned({ format: g, secret: "", clear_secret: true }, { has_secret: true }),
    ).toBe(true);
    expect(
      willSendUnsigned({ format: g, secret: "", clear_secret: false }, { has_secret: false }),
    ).toBe(true);
  });

  it("チャット形式は署名を使わないので警告しない", () => {
    expect(willSendUnsigned({ format: "discord", secret: "", clear_secret: false }, null)).toBe(
      false,
    );
  });
});

describe("webhook の形式", () => {
  it("選択肢と表示名", () => {
    expect(WEBHOOK_FORMAT_OPTIONS.map((o) => o.value)).toEqual([
      "generic",
      "discord",
      "slack",
      "teams",
    ]);
    expect(webhookFormatLabel("teams")).toBe("Microsoft Teams");
    expect(webhookFormatLabel("generic")).toBe("millicall JSON");
    expect(isChatFormat("slack")).toBe(true);
    expect(isChatFormat("generic")).toBe(false);
    expect(isChatFormat("unknown")).toBe(false);
  });

  it("形式ごとに URL のプレースホルダを切り替える", () => {
    expect(webhookFormatOption("discord").placeholder).toContain("https://discord.com/api/webhooks/");
    expect(webhookFormatOption("slack").placeholder).toContain("https://hooks.slack.com/services/");
    expect(webhookFormatOption("teams").placeholder).toContain("Power Automate");
    // 未知の値は generic 扱い
    expect(webhookFormatOption("x").value).toBe("generic");
  });

  it("新規は generic、編集は保存済みの形式で始まる", () => {
    expect(emptyWebhookForm().format).toBe("generic");
    expect(formFromWebhook({ ...original, format: "slack" }).format).toBe("slack");
  });

  it("作成 payload: 形式を含め、チャット形式ではシークレットを送らない", () => {
    const base = { ...emptyWebhookForm(), name: "n", url: "https://hooks.slack.com/services/x" };
    expect(buildCreatePayload({ ...base, format: "slack", secret: "left-over" })).toMatchObject({
      format: "slack",
      secret: "",
    });
    expect(buildCreatePayload({ ...base, secret: "k" })).toMatchObject({
      format: "generic",
      secret: "k",
    });
  });

  it("更新 payload: 形式の変更を送り、チャット形式ではシークレットを触らない", () => {
    const form = formFromWebhook(original);
    expect(buildUpdatePayload({ ...form, format: "teams" }, original)).toEqual({ format: "teams" });
    expect(
      buildUpdatePayload({ ...form, format: "discord", secret: "x", clear_secret: true }, original),
    ).toEqual({ format: "discord" });
    const chat = { ...original, format: "discord" };
    expect(buildUpdatePayload(formFromWebhook(chat), chat)).toEqual({});
  });

  it("チャット形式ではシークレットの長さを検証しない", () => {
    const base = { ...emptyWebhookForm(), name: "x", url: "https://a.example/", secret: "k".repeat(300) };
    expect(validateWebhookForm(base).secret).toBeTruthy();
    expect(validateWebhookForm({ ...base, format: "discord" }).secret).toBeUndefined();
  });
});

describe("ルーティング: FAX 受信", () => {
  it("番号プランの種別（トランクの着信先 select）に FAX 受信がある", () => {
    expect(NUMBER_PLAN_KIND_LABELS.fax).toBe("FAX 受信");
    expect(numberPlanKindLabel("fax")).toBe("FAX 受信");
  });

  it("FAX 受信番号フォームの検証と payload", () => {
    expect(validateFaxReceiverForm({ number: "300", name: "FAX", enabled: true })).toEqual({});
    expect(validateFaxReceiverForm({ number: "3", name: "FAX", enabled: true }).number).toBeTruthy();
    expect(buildFaxReceiverPayload({ number: " 300 ", name: " 代表 ", enabled: false })).toEqual({
      number: "300",
      name: "代表",
      enabled: false,
    });
  });
});
