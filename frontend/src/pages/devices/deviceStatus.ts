/**
 * デバイスの配信状態の判定と、再取得指示（resync）結果のトースト文言（issue #85）。
 *
 * DOM 非依存の純粋関数にして vitest で検証する。
 *
 * 「完了」は「端末固有の設定ファイルを取得済み」かつ「SIP 登録を確認済み」のときだけ。
 * SIP 登録状態を FreeSWITCH から取得できない場合は「登録未確認」とし、完了と偽らない。
 */

export type DeviceStatusInput = {
  id: number;
  provisioned: boolean;
  provision_mode: "standard" | "strict";
  token_issued: boolean;
  last_config_fetch_at?: string | null;
  last_config_fetch_used_token: boolean;
};

/** GET /api/devices/registrations の結果（未取得・取得失敗は null）。 */
export type RegistrationsInput = {
  available: boolean;
  registered_device_ids: number[];
} | null;

export type DeviceStatusKind =
  | "unassigned" // 内線未割当
  | "token_not_issued" // 厳格モードでトークン未発行
  | "token_waiting" // 厳格モードでトークン付き URL からの取得待ち
  | "fetch_waiting" // 設定ファイルの取得待ち
  | "fetched_unverified" // 取得済み・SIP 登録未確認
  | "fetched_unregistered" // 取得済み・SIP 未登録
  | "complete"; // 取得済み・SIP 登録済み

export type DeviceStatus = {
  kind: DeviceStatusKind;
  label: string;
  tone: "success" | "warn" | "neutral" | "danger";
  hint: string;
};

export function deviceStatus(device: DeviceStatusInput, regs: RegistrationsInput): DeviceStatus {
  if (!device.provisioned) {
    return { kind: "unassigned", label: "未割当", tone: "neutral", hint: "内線を割り当ててください" };
  }
  const strict = device.provision_mode === "strict";
  if (strict && !device.token_issued) {
    return {
      kind: "token_not_issued",
      label: "トークン未発行",
      tone: "danger",
      hint: "厳格モードではトークンを発行して電話機に入力するまで設定を配信しません",
    };
  }
  // 厳格モードではトークン付き URL での取得のみを「取得済み」とみなす
  const fetched = Boolean(device.last_config_fetch_at) && (!strict || device.last_config_fetch_used_token);
  if (!fetched) {
    return strict
      ? {
          kind: "token_waiting",
          label: "トークン待ち",
          tone: "warn",
          hint: "トークン付き URL を電話機の Web 画面に入力し、取得を待っています",
        }
      : {
          kind: "fetch_waiting",
          label: "取得待ち",
          tone: "warn",
          hint: "電話機が設定ファイルを取得するのを待っています",
        };
  }
  if (!regs || !regs.available) {
    return {
      kind: "fetched_unverified",
      label: "取得済み（登録未確認）",
      tone: "warn",
      hint: "FreeSWITCH から SIP 登録状態を取得できませんでした",
    };
  }
  if (!regs.registered_device_ids.includes(device.id)) {
    return {
      kind: "fetched_unregistered",
      label: "取得済み（未登録）",
      tone: "warn",
      hint: "設定ファイルは取得済みですが、この端末の IP からの SIP 登録がありません",
    };
  }
  return { kind: "complete", label: "完了", tone: "success", hint: "設定取得済み・SIP 登録済み" };
}

export type ResyncAttemptInput = {
  method: string;
  ok: boolean;
  detail: string;
  verified: boolean;
  skipped: boolean;
};

export type ResyncResultInput = {
  requested: boolean;
  attempts: ResyncAttemptInput[];
};

const METHOD_LABELS: Record<string, string> = {
  sip_notify_check_sync: "SIP NOTIFY",
  yealink_action_uri: "Yealink Action URI",
  panasonic_admin_resync: "Panasonic /admin/resync",
  panasonic_cgi_provision: "Panasonic CGI",
  http: "HTTP",
};

export function resyncMethodLabel(method: string): string {
  return METHOD_LABELS[method] ?? method;
}

/** 再取得指示の結果をトースト用の文言とトーンにする。 */
export function resyncToast(result: ResyncResultInput): { tone: "success" | "danger"; message: string } {
  if (result.requested) {
    const used = result.attempts
      .filter((a) => a.ok)
      .map((a) => resyncMethodLabel(a.method) + (a.verified ? "" : "（未検証）"));
    return {
      tone: "success",
      message: `再取得を指示しました（${used.join("・")}）。取得できたかは状態列で確認してください`,
    };
  }
  const reasons = result.attempts.map((a) => `${resyncMethodLabel(a.method)}: ${a.detail}`);
  return {
    tone: "danger",
    message:
      reasons.length > 0
        ? `再取得を指示できませんでした（${reasons.join(" / ")}）`
        : "再取得を指示できませんでした",
  };
}
