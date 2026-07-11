/**
 * SCIM グループ → ロール割り当ての key-value 編集 UI。
 *
 * ワークフロー ConfigPanel の key_value_list に似た行追加式:
 * 「グループ名（テキスト） + ロール（select） + 削除」の行を並べ、
 * 「+ 行を追加」で空行を足す。値は rows JSON（scimRoleMap.ts 参照）で受け渡す。
 */

import { css, cx } from "styled-system/css";
import { input } from "styled-system/recipes";

import { parseRowsJson, SCIM_ROLE_OPTIONS, type ScimRoleMapRow } from "./scimRoleMap";

export function ScimRoleMapField({
  value,
  onChange,
}: {
  /** rows JSON（FormState 保持値）。 */
  value: string;
  onChange: (rowsJson: string) => void;
}) {
  const rows = parseRowsJson(value);

  const update = (next: ScimRoleMapRow[]) => onChange(JSON.stringify(next));

  return (
    <div className={css({ display: "flex", flexDirection: "column", gap: "2" })}>
      {rows.length === 0 ? (
        <p className={css({ fontSize: "sm", color: "text.subtle" })}>
          割り当てはありません。IdP のグループ名とロールの対応を追加してください。
        </p>
      ) : null}
      {rows.map((row, i) => (
        <div key={i} className={css({ display: "flex", gap: "2", alignItems: "center" })}>
          <input
            className={input()}
            value={row.group}
            placeholder="グループ名（displayName）"
            style={{ flex: 2 }}
            onChange={(e) =>
              update(rows.map((r, j) => (j === i ? { ...r, group: e.target.value } : r)))
            }
          />
          <select
            className={cx(input(), css({ flex: 1 }))}
            value={row.role}
            onChange={(e) =>
              update(rows.map((r, j) => (j === i ? { ...r, role: e.target.value } : r)))
            }
          >
            {SCIM_ROLE_OPTIONS.map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
          <button
            type="button"
            aria-label="この割り当てを削除"
            onClick={() => update(rows.filter((_, j) => j !== i))}
            className={css({
              flexShrink: 0,
              width: "28px",
              height: "28px",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "lg",
              color: "text.muted",
              bg: "transparent",
              border: "none",
              cursor: "pointer",
              borderRadius: "sm",
              _hover: { bg: "danger.soft", color: "danger.text" },
            })}
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => update([...rows, { group: "", role: "user" }])}
        className={css({
          fontSize: "sm",
          color: "text.muted",
          bg: "transparent",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
          px: "0",
          _hover: { color: "text" },
        })}
      >
        + 行を追加
      </button>
    </div>
  );
}
