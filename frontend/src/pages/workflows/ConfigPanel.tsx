/**
 * ノード設定パネル（右ドロワー内）。
 * config_schema を走査して動的フォームを生成する（Phase 4b Task 10）。
 */
import { useQuery } from "@tanstack/react-query";

import { css } from "styled-system/css";
import { input } from "styled-system/recipes";

import { api } from "../../api/client";
import { AGENTS_KEY, PROVIDERS_KEY } from "../../queryKeys";
import type { AgentOption, ConfigSchemaField, ProviderOption } from "./types";

interface ConfigPanelProps {
  /** ノード ID（表示用）。 */
  nodeId: string;
  /** ノードの日本語ラベル。 */
  nodeLabel: string;
  /** 現在の config 値。 */
  config: Record<string, unknown>;
  /** config_schema 定義。 */
  schema: ConfigSchemaField[];
  /** config が変わったとき呼ばれる。 */
  onChange: (key: string, value: unknown) => void;
  /** ノードを削除するボタン用コールバック。 */
  onDelete: () => void;
}

export function ConfigPanel({
  nodeId,
  nodeLabel,
  config,
  schema,
  onChange,
  onDelete,
}: ConfigPanelProps) {
  const providersQuery = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/providers");
      if (error) throw new Error("プロバイダ取得失敗");
      return (data ?? []) as ProviderOption[];
    },
  });

  const agentsQuery = useQuery({
    queryKey: AGENTS_KEY,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/ai-agents");
      if (error) throw new Error("AIエージェント取得失敗");
      return (data ?? []) as AgentOption[];
    },
  });

  const providers = providersQuery.data ?? [];
  const agents = agentsQuery.data ?? [];

  return (
    <div
      className={css({ display: "flex", flexDirection: "column", gap: "1", height: "100%" })}
    >
      {/* ノードヘッダ */}
      <div
        className={css({
          pb: "3",
          mb: "3",
          borderBottomWidth: "1px",
          borderBottomStyle: "solid",
          borderBottomColor: "border",
        })}
      >
        <p className={css({ fontSize: "lg", fontWeight: "600", color: "text" })}>{nodeLabel}</p>
        <p className={css({ fontSize: "xs", color: "text.muted", fontFamily: "monospace", mt: "0.5" })}>
          {nodeId}
        </p>
      </div>

      {/* 動的フィールド */}
      <div className={css({ display: "flex", flexDirection: "column", gap: "4", flex: 1, overflowY: "auto" })}>
        {schema.map((field) => (
          <SchemaField
            key={field.key}
            field={field}
            value={config[field.key]}
            onChange={(v) => onChange(field.key, v)}
            providers={providers}
            agents={agents}
          />
        ))}
        {schema.length === 0 && (
          <p className={css({ color: "text.muted", fontSize: "sm" })}>設定項目はありません。</p>
        )}
      </div>

      {/* 削除ボタン */}
      <div
        className={css({
          pt: "4",
          mt: "3",
          borderTopWidth: "1px",
          borderTopStyle: "solid",
          borderTopColor: "border",
        })}
      >
        <button
          type="button"
          onClick={onDelete}
          className={css({
            width: "100%",
            height: "36px",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "sm",
            fontWeight: "500",
            color: "danger.text",
            bg: "transparent",
            borderWidth: "1px",
            borderStyle: "solid",
            borderColor: "danger",
            borderRadius: "md",
            cursor: "pointer",
            _hover: { bg: "danger.soft" },
          })}
        >
          このノードを削除
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
// 個別フィールドレンダラ
// ─────────────────────────────────────────────────────────

interface FieldProps {
  field: ConfigSchemaField;
  value: unknown;
  onChange: (v: unknown) => void;
  providers: ProviderOption[];
  agents: AgentOption[];
}

function SchemaField({ field, value, onChange, providers, agents }: FieldProps) {
  const labelEl = (
    <span className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}>
      {field.label}
      {field.required && (
        <span className={css({ color: "danger.text", ml: "0.5" })}>*</span>
      )}
    </span>
  );

  const strVal = value === undefined || value === null ? "" : String(value);

  switch (field.type) {
    case "string":
      return (
        <label className={css({ display: "block" })}>
          {labelEl}
          <input
            className={input()}
            value={strVal}
            onChange={(e) => onChange(e.target.value)}
            placeholder={field.description}
          />
        </label>
      );

    case "textarea":
      return (
        <label className={css({ display: "block" })}>
          {labelEl}
          <textarea
            className={input()}
            value={strVal}
            onChange={(e) => onChange(e.target.value)}
            rows={3}
            style={{ resize: "vertical", width: "100%" }}
            placeholder={field.description}
          />
        </label>
      );

    case "number":
      return (
        <label className={css({ display: "block" })}>
          {labelEl}
          <input
            type="number"
            className={input()}
            value={strVal}
            onChange={(e) => onChange(Number(e.target.value))}
          />
        </label>
      );

    case "boolean":
      return (
        <label
          className={css({
            display: "flex",
            alignItems: "center",
            gap: "2",
            cursor: "pointer",
            fontSize: "sm",
          })}
        >
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => onChange(e.target.checked)}
          />
          {field.label}
        </label>
      );

    case "select":
      return (
        <label className={css({ display: "block" })}>
          {labelEl}
          <select
            className={input()}
            value={strVal}
            onChange={(e) => onChange(e.target.value)}
          >
            {!field.required && <option value="">-- 選択してください --</option>}
            {(field.options ?? []).map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </label>
      );

    case "multi_select": {
      const arr = Array.isArray(value) ? (value as string[]) : [];
      return (
        <div>
          {labelEl}
          <div className={css({ display: "flex", flexWrap: "wrap", gap: "2" })}>
            {(field.options ?? []).map((opt) => {
              const checked = arr.includes(opt);
              return (
                <label
                  key={opt}
                  className={css({
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "1",
                    fontSize: "sm",
                    cursor: "pointer",
                  })}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(e) => {
                      if (e.target.checked) {
                        onChange([...arr, opt]);
                      } else {
                        onChange(arr.filter((x) => x !== opt));
                      }
                    }}
                  />
                  {opt}
                </label>
              );
            })}
          </div>
        </div>
      );
    }

    case "provider_ref": {
      const filtered = field.provider_type
        ? providers.filter((p) => p.type === field.provider_type)
        : providers;
      const numVal = value === null || value === undefined ? "" : String(value);
      return (
        <label className={css({ display: "block" })}>
          {labelEl}
          <select
            className={input()}
            value={numVal}
            onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">-- 選択してください --</option>
            {filtered.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      );
    }

    case "agent_ref": {
      const numVal = value === null || value === undefined ? "" : String(value);
      return (
        <label className={css({ display: "block" })}>
          {labelEl}
          <select
            className={input()}
            value={numVal}
            onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">-- 選択してください --</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
      );
    }

    case "key_value_list": {
      const obj = (typeof value === "object" && value !== null && !Array.isArray(value))
        ? (value as Record<string, string>)
        : {};
      const entries = Object.entries(obj);
      return (
        <div>
          {labelEl}
          <div className={css({ display: "flex", flexDirection: "column", gap: "2" })}>
            {entries.map(([k, v], i) => (
              <div key={i} className={css({ display: "flex", gap: "2", alignItems: "center" })}>
                <input
                  className={input()}
                  value={k}
                  placeholder="キー"
                  style={{ flex: 1 }}
                  onChange={(e) => {
                    const newObj = Object.fromEntries(
                      entries.map(([ek, ev], j) => (j === i ? [e.target.value, ev] : [ek, ev])),
                    );
                    onChange(newObj);
                  }}
                />
                <input
                  className={input()}
                  value={v}
                  placeholder="値"
                  style={{ flex: 2 }}
                  onChange={(e) => {
                    const newObj = Object.fromEntries(
                      entries.map(([ek, ev], j) => (j === i ? [ek, e.target.value] : [ek, ev])),
                    );
                    onChange(newObj);
                  }}
                />
                <button
                  type="button"
                  onClick={() => {
                    const newEntries = entries.filter((_, j) => j !== i);
                    onChange(Object.fromEntries(newEntries));
                  }}
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
              onClick={() => {
                const newObj = { ...obj, "": "" };
                onChange(newObj);
              }}
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
        </div>
      );
    }

    case "json": {
      const jsonStr =
        typeof value === "string"
          ? value
          : JSON.stringify(value ?? {}, null, 2);
      return (
        <label className={css({ display: "block" })}>
          {labelEl}
          <textarea
            className={input()}
            value={jsonStr}
            rows={4}
            style={{ resize: "vertical", width: "100%", fontFamily: "monospace", fontSize: 12 }}
            onChange={(e) => {
              try {
                onChange(JSON.parse(e.target.value));
              } catch {
                onChange(e.target.value);
              }
            }}
          />
        </label>
      );
    }

    default:
      return (
        <label className={css({ display: "block" })}>
          {labelEl}
          <input className={input()} value={strVal} onChange={(e) => onChange(e.target.value)} />
        </label>
      );
  }
}
