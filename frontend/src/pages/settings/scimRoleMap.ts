/**
 * SCIM グループ → ロール割り当て（scim_group_role_map）のフォーム変換ヘルパー。
 *
 * SettingsPage の FormState は string | boolean のため、編集中の行リストを
 * JSON 文字列（rows JSON）で保持する。行リスト表現なら「グループ名が空のままの
 * 編集途中の行」も UI 状態として保持できる（map 表現だと空キーが潰れてしまう）。
 *
 *   サーバー値 Record<string, string>  --roleMapToRowsJson-->  rows JSON（フォーム保持用）
 *   rows JSON  --rowsJsonToRoleMap-->  Record<string, string>（PUT payload 用・検証込み）
 */

export type ScimRoleMapRow = { group: string; role: string };

/** 割り当て可能なロール（バックエンドの allowlist 検証と一致させること）。 */
export const SCIM_ROLE_OPTIONS = ["user", "admin"] as const;

const DEFAULT_ROLE = "user";

/** サーバーから受け取った map 値をフォーム保持用の rows JSON に変換する。 */
export function roleMapToRowsJson(value: unknown): string {
  const rows: ScimRoleMapRow[] = [];
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    for (const [group, role] of Object.entries(value as Record<string, unknown>)) {
      rows.push({ group, role: typeof role === "string" ? role : DEFAULT_ROLE });
    }
  }
  return JSON.stringify(rows);
}

/** rows JSON をパースする（不正な値は空リストにフォールバック）。 */
export function parseRowsJson(raw: string | boolean): ScimRoleMapRow[] {
  if (typeof raw !== "string" || raw === "") return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map((r) => {
      const row = (typeof r === "object" && r !== null ? r : {}) as Record<string, unknown>;
      return {
        group: typeof row.group === "string" ? row.group : "",
        role: typeof row.role === "string" ? row.role : DEFAULT_ROLE,
      };
    });
  } catch {
    return [];
  }
}

/**
 * rows JSON を PUT payload 用の map に変換する。
 *
 * - グループ名は trim する。空のままの行は「編集途中」とみなして除外する。
 * - 同名グループの重複は Error（どちらの行が有効か判別できないため）。
 */
export function rowsJsonToRoleMap(raw: string | boolean): Record<string, string> {
  const map: Record<string, string> = {};
  for (const row of parseRowsJson(raw)) {
    const group = row.group.trim();
    if (group === "") continue;
    if (group in map) {
      throw new Error(`グループ名が重複しています: 「${group}」`);
    }
    map[group] = row.role;
  }
  return map;
}
