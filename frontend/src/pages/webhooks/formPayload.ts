import type { components } from "../../api/schema";

export type WebhookRead = components["schemas"]["WebhookRead"];
export type WebhookCreate = components["schemas"]["WebhookCreate"];
export type WebhookUpdate = components["schemas"]["WebhookUpdate"];
export type WebhookDeliveryRead = components["schemas"]["WebhookDeliveryRead"];
export type WebhookTestResult = components["schemas"]["WebhookTestResult"];
export type WebhookEvent = NonNullable<WebhookCreate["events"]>[number];
export type WebhookFormat = NonNullable<WebhookCreate["format"]>;

/** 本文の形式（select の表示順）。チャット形式は署名なしで各サービス向けの本文を送る。 */
export const WEBHOOK_FORMAT_OPTIONS: {
  value: WebhookFormat;
  label: string;
  placeholder: string;
  hint: string;
}[] = [
  {
    value: "generic",
    label: "millicall JSON",
    placeholder: "https://example.com/millicall/webhook",
    hint: "自作の受信サーバー向け。イベントの JSON をそのまま送り、シークレットで署名します。",
  },
  {
    value: "discord",
    label: "Discord",
    placeholder: "https://discord.com/api/webhooks/...",
    hint: "チャンネルの設定 → 連携サービス → ウェブフック で作成した URL を貼り付けてください。",
  },
  {
    value: "slack",
    label: "Slack",
    placeholder: "https://hooks.slack.com/services/...",
    hint: "Slack アプリの Incoming Webhooks で作成した URL を貼り付けてください。",
  },
  {
    value: "teams",
    label: "Microsoft Teams",
    placeholder: "Power Automate の Webhook URL",
    hint: "Teams の「ワークフロー」で「Webhook 要求を受信したらチャネルに投稿する」を作成し、その URL を貼り付けてください。",
  },
];

export function isChatFormat(format: string): boolean {
  return format !== "generic" && WEBHOOK_FORMAT_OPTIONS.some((o) => o.value === format);
}

export function webhookFormatOption(format: string) {
  return WEBHOOK_FORMAT_OPTIONS.find((o) => o.value === format) ?? WEBHOOK_FORMAT_OPTIONS[0];
}

export function webhookFormatLabel(format: string): string {
  return WEBHOOK_FORMAT_OPTIONS.find((o) => o.value === format)?.label ?? format;
}

/** 購読可能なイベントと日本語ラベル（チェックボックスの表示順）。 */
export const WEBHOOK_EVENT_OPTIONS: { value: WebhookEvent; label: string; help: string }[] = [
  { value: "call.inbound", label: "着信", help: "外線トランクに着信したとき" },
  { value: "call.ended", label: "通話終了", help: "通話が終了し通話履歴が確定したとき" },
  { value: "fax.received", label: "FAX 受信", help: "FAX の受信処理が完了したとき" },
  { value: "fax.sent", label: "FAX 送信完了", help: "FAX の送信に成功したとき" },
  {
    value: "fax.failed",
    label: "FAX 送信失敗",
    help: "再試行しても FAX を送信できなかったとき（最終的な失敗）",
  },
];

export function webhookEventLabel(event: string): string {
  if (event === "webhook.test") return "テスト送信";
  return WEBHOOK_EVENT_OPTIONS.find((o) => o.value === event)?.label ?? event;
}

export interface WebhookFormValues {
  name: string;
  url: string;
  format: WebhookFormat;
  /** 書き込み専用。編集時は空 = 据え置き。チャット形式では使わない。 */
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
    format: "generic",
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
    format: webhookFormatOption(hook.format).value,
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
    format: form.format,
    // チャット形式はシークレット欄を出さないので、切り替え前の入力が残っていても送らない。
    secret: isChatFormat(form.format) ? "" : form.secret,
    events: [...form.events],
    enabled: form.enabled,
  };
}

/**
 * PATCH payload。変更のない項目は送らない。secret は空なら据え置き、削除指定なら "" を送る。
 * チャット形式では secret を送らない（保存済みのシークレットは据え置き、generic に戻すと再び使われる）。
 */
export function buildUpdatePayload(form: WebhookFormValues, original: WebhookRead): WebhookUpdate {
  const payload: WebhookUpdate = {};
  const name = form.name.trim();
  if (name !== original.name) payload.name = name;
  const url = form.url.trim();
  if (url !== original.url) payload.url = url;
  if (form.format !== original.format) payload.format = form.format;
  if (!isChatFormat(form.format)) {
    if (form.clear_secret) payload.secret = "";
    else if (form.secret !== "") payload.secret = form.secret;
  }
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
  if (!isChatFormat(form.format) && form.secret.length > 256) errors.secret = "シークレットは 256 文字以内で入力してください";
  if (form.events.length === 0) errors.events = "通知するイベントを 1 つ以上選択してください";
  return errors;
}

/**
 * 保存すると署名なしで送信される状態か（シークレット未設定の警告表示に使う）。
 * チャット形式は常に署名なしだが仕様どおりなので警告しない（false）。
 */
export function willSendUnsigned(
  form: Pick<WebhookFormValues, "format" | "secret" | "clear_secret">,
  original: Pick<WebhookRead, "has_secret"> | null,
): boolean {
  if (isChatFormat(form.format)) return false;
  if (form.clear_secret) return true;
  return form.secret === "" && !original?.has_secret;
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
