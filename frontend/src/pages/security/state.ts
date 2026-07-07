/**
 * 2FA 登録フローのステートマシン（テスト可能なピュアロジック）。
 *
 * 状態遷移:
 *   idle ──setup──▶ enrolling（secret / provisioning_uri 取得済み）
 *   enrolling ──verify成功──▶ recovery（リカバリコード表示）
 *   recovery ──done──▶ idle
 *
 * すでに 2FA 有効な場合の再セットアップ・無効化は現行コードが必要。
 */

export type TotpEnrollState =
  | { step: "idle" }
  | { step: "enrolling"; secret: string; provisioningUri: string }
  | { step: "recovery"; recoveryCodes: string[] };

export function initialState(): TotpEnrollState {
  return { step: "idle" };
}

export function toEnrolling(secret: string, provisioningUri: string): TotpEnrollState {
  return { step: "enrolling", secret, provisioningUri };
}

export function toRecovery(recoveryCodes: string[]): TotpEnrollState {
  return { step: "recovery", recoveryCodes };
}

/**
 * setup リクエストのボディを組み立てる。
 * 既に 2FA 有効な場合は現行コードが必須（本人再確認）。初回は body なし。
 */
export function buildSetupBody(
  totpEnabled: boolean,
  reauthCode: string
): { code: string } | undefined {
  if (!totpEnabled) return undefined;
  return { code: reauthCode.trim() };
}

/** 6 桁の TOTP コードか（数字のみ 6 文字）を判定する。 */
export function isValidTotpCode(code: string): boolean {
  return /^\d{6}$/.test(code.trim());
}
