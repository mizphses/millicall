import type { components } from "../../api/schema";

export type TrunkRead = components["schemas"]["TrunkRead"];
export type TrunkCreate = components["schemas"]["TrunkCreate"];
export type TrunkUpdate = components["schemas"]["TrunkUpdate"];

/** SlidePanel フォームが保持する値。API 型とは分離し、UI 都合の型に寄せる。 */
export interface TrunkFormValues {
  /** 作成時のみ使用。英数字・ハイフン・アンダースコア 1〜50 文字。 */
  name: string;
  display_name: string;
  host: string;
  username: string;
  /**
   * 書き込み専用。
   * - 作成時: 必須
   * - 編集時: 空ならペイロードに含めない（= 現状のパスワードを据え置き）
   */
  password: string;
  did_number: string;
  caller_id: string;
  /** 着信先内線番号。空文字 = 着信しない（番号プランへ振り分けない）。 */
  inbound_extension: string;
  /**
   * 送信元 SIP ポート。空文字 = 自動採番（サーバが external_sip_port から採番）。
   * 数値文字列で保持し、payload 変換時に number | null へ写す。
   */
  source_port: string;
  /** トランク種別。hgw=LAN 内 HGW（既定）/ sip=インターネット越しの SIP プロバイダ。 */
  trunk_type: "hgw" | "sip";
  /**
   * SIP 種別の着信許可 CIDR。改行/カンマ区切りテキストで保持し、
   * payload 変換時に配列へ split する。空 = ACL を掛けない。
   */
  inbound_cidrs: string;
  enabled: boolean;
}

/** CIDR テキスト（改行/カンマ区切り）を配列へ分解する。空要素は除去。 */
function parseCidrs(raw: string): string[] {
  return raw
    .split(/[\n,]/)
    .map((c) => c.trim())
    .filter((c) => c !== "");
}

/** 作成フォームの初期値。 */
export function emptyForm(): TrunkFormValues {
  return {
    name: "",
    display_name: "",
    host: "",
    username: "",
    password: "",
    did_number: "",
    caller_id: "",
    inbound_extension: "",
    source_port: "",
    trunk_type: "hgw",
    inbound_cidrs: "",
    enabled: true,
  };
}

/** 既存トランクを編集フォーム値へ写像する。password は読み出せないため空にする。 */
export function formFromTrunk(trunk: TrunkRead): TrunkFormValues {
  return {
    name: trunk.name,
    display_name: trunk.display_name,
    host: trunk.host,
    username: trunk.username,
    password: "",         // write-only: サーバから取得不可
    did_number: trunk.did_number,
    caller_id: trunk.caller_id,
    inbound_extension: trunk.inbound_extension,
    source_port: trunk.source_port != null ? String(trunk.source_port) : "",
    trunk_type: trunk.trunk_type,
    inbound_cidrs: trunk.inbound_cidrs.join("\n"),
    enabled: trunk.enabled,
  };
}

/** 作成 payload への変換。全フィールドを含める。 */
export function buildCreatePayload(form: TrunkFormValues): TrunkCreate {
  return {
    name: form.name.trim(),
    display_name: form.display_name.trim(),
    host: form.host.trim(),
    username: form.username.trim(),
    password: form.password,
    did_number: form.did_number.trim(),
    caller_id: form.caller_id.trim(),
    inbound_extension: form.inbound_extension.trim(),
    // 空文字 = 自動採番（null）。数値文字列なら number として送る。
    source_port: parseSourcePort(form.source_port),
    trunk_type: form.trunk_type,
    inbound_cidrs: parseCidrs(form.inbound_cidrs),
    enabled: form.enabled,
  };
}

/** フォームの送信元ポート文字列を number | null に変換する。空 = null（自動採番）。 */
function parseSourcePort(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  return Number(trimmed);
}

/**
 * 編集フォーム → PATCH payload 変換。
 *
 * omit-if-unchanged: 変更のないフィールドはペイロードに含めない。
 * omit-if-empty（秘密フィールド）: password は空なら含めない（= 据え置き）。
 *
 * - display_name / host / username: 空か unchanged なら含めない
 * - password: 書き込み専用。空なら据え置き（含めない）、非空なら含める（original との比較なし）
 * - did_number / caller_id / inbound_extension: 空文字も有効値。変更があれば含める
 * - enabled: boolean 比較
 */
export function buildUpdatePayload(
  form: TrunkFormValues,
  original: TrunkRead,
): TrunkUpdate {
  const payload: TrunkUpdate = {};

  const displayName = form.display_name.trim();
  if (displayName !== "" && displayName !== original.display_name) {
    payload.display_name = displayName;
  }

  const host = form.host.trim();
  if (host !== "" && host !== original.host) {
    payload.host = host;
  }

  const username = form.username.trim();
  if (username !== "" && username !== original.username) {
    payload.username = username;
  }

  // password: write-only。空なら据え置き、非空なら送る。original との比較は不要。
  if (form.password !== "") {
    payload.password = form.password;
  }

  const didNumber = form.did_number.trim();
  if (didNumber !== original.did_number) {
    payload.did_number = didNumber;
  }

  const callerId = form.caller_id.trim();
  if (callerId !== original.caller_id) {
    payload.caller_id = callerId;
  }

  // inbound_extension: 空文字 = 着信しない（有効値）。変更があれば含める。
  const inboundExtension = form.inbound_extension.trim();
  if (inboundExtension !== original.inbound_extension) {
    payload.inbound_extension = inboundExtension;
  }

  // source_port: 空 = 自動採番(null)。変更があれば含める（null 明示で自動採番に戻す）。
  const sourcePort = parseSourcePort(form.source_port);
  if (sourcePort !== (original.source_port ?? null)) {
    payload.source_port = sourcePort;
  }

  if (form.trunk_type !== original.trunk_type) {
    payload.trunk_type = form.trunk_type;
  }

  // inbound_cidrs: 配列比較。順序含め異なれば含める。
  const cidrs = parseCidrs(form.inbound_cidrs);
  if (cidrs.join(",") !== original.inbound_cidrs.join(",")) {
    payload.inbound_cidrs = cidrs;
  }

  if (form.enabled !== original.enabled) {
    payload.enabled = form.enabled;
  }

  return payload;
}

const NAME_PATTERN = /^[A-Za-z0-9_-]{1,50}$/;

/** クライアント側のバリデーション。フィールド名 → エラーメッセージ。 */
export function validateForm(
  form: TrunkFormValues,
  mode: "create" | "edit",
): Partial<Record<keyof TrunkFormValues, string>> {
  const errors: Partial<Record<keyof TrunkFormValues, string>> = {};

  if (mode === "create") {
    if (!NAME_PATTERN.test(form.name.trim())) {
      errors.name = "名前は英数字・ハイフン・アンダースコアで 1〜50 文字です";
    }
    if (form.display_name.trim().length < 1) {
      errors.display_name = "表示名を入力してください";
    }
    if (form.host.trim().length < 1) {
      errors.host = "ホスト名を入力してください";
    }
    if (form.username.trim().length < 1) {
      errors.username = "ユーザー名を入力してください";
    }
    if (form.password.length < 1) {
      errors.password = "パスワードを入力してください";
    }
  } else {
    // 編集: 空なら据え置き（エラーなし）。入力があれば長さをチェック。
    const displayName = form.display_name.trim();
    if (displayName.length > 100) {
      errors.display_name = "表示名は 100 文字以内で入力してください";
    }
    const host = form.host.trim();
    if (host.length > 100) {
      errors.host = "ホスト名は 100 文字以内で入力してください";
    }
    const username = form.username.trim();
    if (username.length > 50) {
      errors.username = "ユーザー名は 50 文字以内で入力してください";
    }
    if (form.password.length > 128) {
      errors.password = "パスワードは 128 文字以内で入力してください";
    }
  }

  if (form.did_number.trim().length > 30) {
    errors.did_number = "DID 番号は 30 文字以内で入力してください";
  }
  if (form.caller_id.trim().length > 30) {
    errors.caller_id = "発信者番号は 30 文字以内で入力してください";
  }

  // 送信元ポート: 空は許可（自動採番）。入力があれば 1024〜65535 の整数。
  const sourcePort = form.source_port.trim();
  if (sourcePort !== "") {
    const n = Number(sourcePort);
    if (!Number.isInteger(n) || n < 1024 || n > 65535) {
      errors.source_port = "送信元ポートは 1024〜65535 の整数で入力してください";
    }
  }

  // 着信許可 CIDR: SIP 種別のみ対象。各要素が IPv4/IPv6 の CIDR/アドレス表記か簡易検証。
  if (form.trunk_type === "sip") {
    const invalid = parseCidrs(form.inbound_cidrs).filter((c) => !isCidrLike(c));
    if (invalid.length > 0) {
      errors.inbound_cidrs = `CIDR 表記が不正です: ${invalid.join(", ")}（例: 203.0.113.0/24）`;
    }
  }

  return errors;
}

/** IPv4/IPv6 のアドレス or CIDR 表記かの簡易チェック（厳密な範囲検証はサーバ側）。 */
function isCidrLike(value: string): boolean {
  const [addr, prefix, ...rest] = value.split("/");
  if (rest.length > 0) return false;
  if (prefix !== undefined) {
    const p = Number(prefix);
    if (!Number.isInteger(p) || p < 0 || p > 128) return false;
  }
  const ipv4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;
  if (ipv4.test(addr)) {
    return addr.split(".").every((o) => Number(o) <= 255);
  }
  // IPv6: 簡易（16 進とコロンのみ）。
  return /^[0-9a-fA-F:]+$/.test(addr) && addr.includes(":");
}
