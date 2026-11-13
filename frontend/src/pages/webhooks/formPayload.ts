import type { components } from "../../api/schema";

export type WebhookRead = components["schemas"]["WebhookRead"];
export type WebhookCreate = components["schemas"]["WebhookCreate"];
export type WebhookUpdate = components["schemas"]["WebhookUpdate"];
export type WebhookDeliveryRead = components["schemas"]["WebhookDeliveryRead"];
export type WebhookTestResult = components["schemas"]["WebhookTestResult"];
export type WebhookEvent = NonNullable<WebhookCreate["events"]>[number];

/** 購読可能なイベントと日本語ラベル（チェックボックスの表示順）。 */
export const WEBHOOK_EVENT_OPTIONS: { value: WebhookEvent; label: string; help: string }[] = [
  { value: "call.inbound", label: "着信", help: "外線トランクに着信したとき" },
  { value: "call.ended", label: "通話終了", help: "通話が終了し通話履歴が確定したとき" },
  { value: "fax.received", label: "FAX 受信", help: "FAX の受信処理が完了したとき" },
];

export function webhookEventLabel(event: string): string {
  if (event === "webhook.test") return "テスト送信";
  return WEBHOOK_EVENT_OPTIONS.find((o) => o.value === event)?.label ?? event;
}

export interface WebhookFormValues {
  name: string;
  url: string;
  /** 書き込み専用。編集時は空 = 据え置き。 */
  secret: string;
  /** 編集時のみ: 既存シークレットを削除して署名なしにする。 */
  clear_secret: boolean;
  events: WebhookEvent[];
  enabled: boolean;
}

export function emptyWebhookForm(): WebhookFormValues {
  return {
    name: "",
    url: "",
    secret: "",
    clear_secret: false,
    events: ["call.inbound", "call.ended", "fax.received"],
    enabled: true,
  };
}

export function formFromWebhook(hook: WebhookRead): WebhookFormValues {
  return {
    name: hook.name,
    url: hook.url,
    secret: "",
    clear_secret: false,
    events: WEBHOOK_EVENT_OPTIONS.map((o) => o.value).filter((e) => hook.events.includes(e)),
    enabled: hook.enabled,
  };
}

/** チェックボックスのトグル（表示順を保つ）。 */
export function toggleEvent(
  events: WebhookEvent[],
  event: WebhookEvent,
  checked: boolean,
): WebhookEvent[] {
  const set = new Set(events);
  if (checked) set.add(event);
  else set.delete(event);
  return WEBHOOK_EVENT_OPTIONS.map((o) => o.value).filter((e) => set.has(e));
}

export function buildCreatePayload(form: WebhookFormValues): WebhookCreate {
  return {
    name: form.name.trim(),
    url: form.url.trim(),
    secret: form.secret,
    events: [...form.events],
    enabled: form.enabled,
  };
}

/** PATCH payload。変更のない項目は送らない。secret は空なら据え置き、削除指定なら "" を送る。 */
export function buildUpdatePayload(form: WebhookFormValues, original: WebhookRead): WebhookUpdate {
  const payload: WebhookUpdate = {};
  const name = form.name.trim();
  if (name !== original.name) payload.name = name;
  const url = form.url.trim();
  if (url !== original.url) payload.url = url;
  if (form.clear_secret) payload.secret = "";
  else if (form.secret !== "") payload.secret = form.secret;
  const before = [...original.events].sort().join(",");
  const after = [...form.events].sort().join(",");
  if (before !== after) payload.events = [...form.events];
  if (form.enabled !== original.enabled) payload.enabled = form.enabled;
  return payload;
}

export function validateWebhookForm(
  form: WebhookFormValues,
): Partial<Record<keyof WebhookFormValues, string>> {
  const errors: Partial<Record<keyof WebhookFormValues, string>> = {};
  const name = form.name.trim();
  if (name.length < 1 || name.length > 100) errors.name = "名前は 1〜100 文字で入力してください";
  const url = form.url.trim();
  let parsed: URL | null = null;
  try {
    parsed = new URL(url);
  } catch {
    parsed = null;
  }
  if (!parsed || (parsed.protocol !== "http:" && parsed.protocol !== "https:")) {
    errors.url = "http:// または https:// で始まる URL を入力してください";
  } else if (parsed.username || parsed.password) {
    errors.url = "URL に認証情報（user:pass@）を含めないでください";
  }
  if (form.secret.length > 256) errors.secret = "シークレットは 256 文字以内で入力してください";
  if (form.events.length === 0) errors.events = "通知するイベントを 1 つ以上選択してください";
  return errors;
}

/** 配信ログの結果表示。 */
export function deliveryStatus(d: Pick<WebhookDeliveryRead, "success" | "status_code" | "error">): {
  label: string;
  tone: "success" | "danger";
} {
  if (d.success) return { label: `成功（${d.status_code ?? "-"}）`, tone: "success" };
  if (d.status_code != null) return { label: `失敗（HTTP ${d.status_code}）`, tone: "danger" };
  return { label: "失敗（接続エラー）", tone: "danger" };
}
