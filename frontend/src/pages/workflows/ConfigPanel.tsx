/**
 * ノード設定パネル（右ドロワー内）。
 * config_schema を走査して動的フォームを生成する（Phase 4b Task 10）。
 */
import { useQuery } from "@tanstack/react-query";
import { Fragment } from "react";

import { css } from "styled-system/css";
import { input } from "styled-system/recipes";

import { api } from "../../api/client";
import {
  AGENTS_KEY,
  BUILTIN_AUDIO_KEY,
  PROVIDERS_KEY,
  WORKFLOWS_KEY,
} from "../../queryKeys";
import {
  rowsFromValue,
  rowsToValue,
  validateKeyValueRows,
  type ConfigDraft,
  type ConfigDrafts,
  type KeyValueRow,
} from "./editorState";
import type {
  AgentOption,
  BuiltinAudioOption,
  ConfigSchemaField,
  ProviderOption,
  WorkflowOption,
} from "./types";

interface ConfigPanelProps {
  /** ノード ID（表示用）。 */
  nodeId: string;
  /** ノードの日本語ラベル。 */
  nodeLabel: string;
  /** バックエンドのノード type（"play_audio" なら組み込み音声セレクトを出す）。 */
  nodeType?: string;
  /** 現在の config 値。 */
  config: Record<string, unknown>;
  /** config_schema 定義。 */
  schema: ConfigSchemaField[];
  /** config が変わったとき呼ばれる。 */
  onChange: (key: string, value: unknown) => void;
  /** ノードを削除するボタン用コールバック。 */
  onDelete: () => void;
  /** 編集中のワークフロー ID（call_workflow の選択肢から自分自身を除外する）。 */
  currentWorkflowId: number;
  drafts: ConfigDrafts;
  onDraftChange: (key: string, draft: ConfigDraft) => void;
}

/** 時間条件ノード（start_time / end_time / days_of_week を持つ）かどうか。 */
function isTimeConditionSchema(schema: ConfigSchemaField[]): boolean {
  const keys = new Set(schema.map((f) => f.key));
  return keys.has("start_time") && keys.has("end_time") && keys.has("days_of_week");
}

/** 時間条件ノードの設定説明と、開始=終了の入力エラー表示。 */
function TimeConditionHint({ config }: { config: Record<string, unknown> }) {
  const sameTime =
    typeof config.start_time === "string" &&
    config.start_time !== "" &&
    config.start_time === config.end_time;
  return (
    <div className={css({ fontSize: "xs", color: "text.muted", lineHeight: "1.6" })}>
      <ul className={css({ listStyle: "disc", pl: "4" })}>
        <li>開始が終了より後の場合は、翌日までの区間として扱います（例: 22:00〜06:00）。</li>
        <li>
          曜日は営業開始日の曜日で判定します（金曜 22:00〜06:00 は土曜 06:00 の直前まで金曜の営業）。
        </li>
        <li>開始と終了を同じ時刻にすることはできません。</li>
      </ul>
      {sameTime && (
        <p role="alert" className={css({ color: "danger.text", mt: "1" })}>
          開始時刻と終了時刻が同じです。保存できません。
        </p>
      )}
    </div>
  );
}

export function ConfigPanel({
  nodeId,
  nodeLabel,
  nodeType,
  config,
  schema,
  onChange,
  onDelete,
  currentWorkflowId,
  drafts,
  onDraftChange,
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

  const workflowsQuery = useQuery({
    queryKey: WORKFLOWS_KEY,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/workflows");
      if (error) throw new Error("ワークフロー取得失敗");
      return (data ?? []) as WorkflowOption[];
    },
  });

  const isPlayAudio = nodeType === "play_audio";
  const builtinAudioQuery = useQuery({
    queryKey: BUILTIN_AUDIO_KEY,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/audio/builtin");
      if (error) throw new Error("組み込み音声取得失敗");
      return (data ?? []) as BuiltinAudioOption[];
    },
    enabled: isPlayAudio,
    staleTime: Infinity,
  });

  const providers = providersQuery.data ?? [];
  const agents = agentsQuery.data ?? [];
  // 自己再帰を防ぐため、編集中のワークフロー自身は呼び出し先候補から外す。
  const workflows = (workflowsQuery.data ?? []).filter(
    (w) => w.id !== currentWorkflowId,
  );

  return (
    <div
      className={css({
        display: "flex",
        flexDirection: "column",
        gap: "1",
        height: "100%",
      })}
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
        <p
          className={css({ fontSize: "lg", fontWeight: "600", color: "text" })}
        >
          {nodeLabel}
        </p>
        <p
          className={css({
            fontSize: "xs",
            color: "text.muted",
            fontFamily: "monospace",
            mt: "0.5",
            overflowWrap: "anywhere",
          })}
        >
          {nodeId}
        </p>
      </div>

      {[
        { query: providersQuery, type: "provider_ref", name: "プロバイダー" },
        { query: agentsQuery, type: "agent_ref", name: "AI エージェント" },
        { query: workflowsQuery, type: "workflow_ref", name: "ワークフロー" },
      ]
        .filter(
          ({ query, type }) =>
            query.isError && schema.some((field) => field.type === type),
        )
        .map(({ query, name }) => (
          <div
            key={name}
            role="alert"
            className={css({ color: "danger.text", fontSize: "sm", mb: "3" })}
          >
            {name}の取得に失敗しました。{" "}
            <button
              type="button"
              onClick={() => void query.refetch()}
              className={css({ textDecoration: "underline" })}
            >
              再試行
            </button>
          </div>
        ))}
      {/* 動的フィールド */}
      <div
        className={css({
          display: "flex",
          flexDirection: "column",
          gap: "4",
          flex: 1,
          overflowY: "auto",
        })}
      >
        {isTimeConditionSchema(schema) && <TimeConditionHint config={config} />}
        {schema.map((field) => (
          <Fragment key={`${nodeId}:${field.key}`}>
            {isPlayAudio && field.key === "file_path" && (
              <BuiltinAudioSelect
                options={builtinAudioQuery.data ?? []}
                isError={builtinAudioQuery.isError}
                onRetry={() => void builtinAudioQuery.refetch()}
                value={config.file_path}
                onChange={(v) => onChange("file_path", v)}
              />
            )}
            <SchemaField
              field={field}
              draft={drafts[`${nodeId}:${field.key}`]}
              onDraftChange={(draft) =>
                onDraftChange(`${nodeId}:${field.key}`, draft)
              }
              value={config[field.key]}
              onChange={(v) => onChange(field.key, v)}
              providers={providers}
              agents={agents}
              workflows={workflows}
            />
          </Fragment>
        ))}
        {schema.length === 0 && (
          <p className={css({ color: "text.muted", fontSize: "sm" })}>
            設定項目はありません。
          </p>
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
// 組み込み音声セレクト（play_audio 専用）
// ─────────────────────────────────────────────────────────

/**
 * 同梱の組み込み音声から選ぶと file_path にその絶対パスを入れる。
 * 「（指定なし）」は組み込み音声が選ばれていたときだけ file_path を空に戻し、
 * 手入力のパスには触れない。file_path の手入力は下の欄で引き続き可能。
 */
function BuiltinAudioSelect({
  options,
  isError,
  onRetry,
  value,
  onChange,
}: {
  options: BuiltinAudioOption[];
  isError: boolean;
  onRetry: () => void;
  value: unknown;
  onChange: (v: string) => void;
}) {
  const current = typeof value === "string" ? value : "";
  const matched = options.find((o) => o.path === current);
  return (
    <label className={css({ display: "block" })}>
      <span
        className={css({
          display: "block",
          fontSize: "sm",
          color: "text.muted",
          mb: "1",
        })}
      >
        組み込み音声
      </span>
      <select
        className={input()}
        value={matched ? matched.id : ""}
        onChange={(e) => {
          const picked = options.find((o) => o.id === e.target.value);
          if (picked) onChange(picked.path);
          else if (matched) onChange("");
        }}
      >
        <option value="">（指定なし）</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
      {isError && (
        <span
          role="alert"
          className={css({
            display: "block",
            color: "danger.text",
            fontSize: "xs",
            mt: "1",
          })}
        >
          組み込み音声の取得に失敗しました。{" "}
          <button
            type="button"
            onClick={onRetry}
            className={css({ textDecoration: "underline" })}
          >
            再試行
          </button>
        </span>
      )}
    </label>
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
  workflows: WorkflowOption[];
  draft?: ConfigDraft;
  onDraftChange: (draft: ConfigDraft) => void;
}

function SchemaField({
  field,
  value,
  onChange,
  providers,
  agents,
  workflows,
  draft,
  onDraftChange,
}: FieldProps) {
  const labelEl = (
    <span
      className={css({
        display: "block",
        fontSize: "sm",
        color: "text.muted",
        mb: "1",
      })}
    >
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
            {!field.required && (
              <option value="">-- 選択してください --</option>
            )}
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
            onChange={(e) =>
              onChange(e.target.value === "" ? null : Number(e.target.value))
            }
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
            onChange={(e) =>
              onChange(e.target.value === "" ? null : Number(e.target.value))
            }
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

    case "workflow_ref": {
      const numVal = value === null || value === undefined ? "" : String(value);
      return (
        <label className={css({ display: "block" })}>
          {labelEl}
          <select
            className={input()}
            value={numVal}
            onChange={(e) =>
              onChange(e.target.value === "" ? null : Number(e.target.value))
            }
          >
            <option value="">-- 選択してください --</option>
            {workflows.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </label>
      );
    }

    case "key_value_list": {
      const rows = draft?.rows ?? rowsFromValue(value);
      const error = draft?.error;
      const updateRows = (next: KeyValueRow[]) => {
        const validation = validateKeyValueRows(next);
        onDraftChange({ rows: next, error: validation });
        if (!validation) onChange(rowsToValue(next));
      };
      return (
        <fieldset className={css({ minWidth: 0, border: "none", p: "0" })}>
          <legend
            className={css({ fontSize: "sm", fontWeight: "600", mb: "2" })}
          >
            {field.label}
          </legend>
          <div
            className={css({
              display: "flex",
              flexDirection: "column",
              gap: "3",
            })}
          >
            {rows.map((row, index) => (
              <div
                key={row.id}
                className={css({
                  display: "grid",
                  gap: "2",
                  gridTemplateColumns: "minmax(0, 1fr) auto",
                  p: "2",
                  borderWidth: "1px",
                  borderColor: "border",
                  borderRadius: "md",
                })}
              >
                <label className={css({ fontSize: "sm", gridColumn: "1" })}>
                  キー {index + 1}
                  <input
                    className={input({ invalid: Boolean(error) })}
                    value={row.key}
                    aria-invalid={Boolean(error)}
                    aria-describedby={error ? `${field.key}-error` : undefined}
                    onChange={(event) =>
                      updateRows(
                        rows.map((item) =>
                          item.id === row.id
                            ? { ...item, key: event.target.value }
                            : item,
                        ),
                      )
                    }
                  />
                </label>
                <button
                  type="button"
                  aria-label={`${index + 1}行目を削除`}
                  onClick={() =>
                    updateRows(rows.filter((item) => item.id !== row.id))
                  }
                  className={css({
                    color: "danger.text",
                    cursor: "pointer",
                    px: "2",
                    alignSelf: "center",
                  })}
                >
                  削除
                </button>
                <label
                  className={css({ fontSize: "sm", gridColumn: "1 / -1" })}
                >
                  値 {index + 1}
                  <input
                    className={input()}
                    value={row.value}
                    onChange={(event) =>
                      updateRows(
                        rows.map((item) =>
                          item.id === row.id
                            ? { ...item, value: event.target.value }
                            : item,
                        ),
                      )
                    }
                  />
                </label>
              </div>
            ))}
            {error && (
              <p
                id={`${field.key}-error`}
                role="alert"
                className={css({ color: "danger.text", fontSize: "sm" })}
              >
                {error}
              </p>
            )}
            <button
              type="button"
              onClick={() =>
                updateRows([
                  ...rows,
                  { id: crypto.randomUUID(), key: "", value: "" },
                ])
              }
              className={css({
                color: "accent.text",
                textAlign: "left",
                cursor: "pointer",
                py: "2",
              })}
            >
              ＋ 行を追加
            </button>
          </div>
        </fieldset>
      );
    }

    case "json": {
      const text =
        draft?.text ??
        (typeof value === "string"
          ? value
          : JSON.stringify(value ?? {}, null, 2));
      return (
        <label className={css({ display: "block" })}>
          {labelEl}
          <textarea
            className={input({ invalid: Boolean(draft?.error) })}
            value={text}
            rows={6}
            aria-invalid={Boolean(draft?.error)}
            aria-describedby={draft?.error ? `${field.key}-error` : undefined}
            style={{
              resize: "vertical",
              width: "100%",
              fontFamily: "monospace",
            }}
            onChange={(event) => {
              const next = event.target.value;
              try {
                const parsed: unknown = JSON.parse(next);
                onDraftChange({ text: next, error: null });
                onChange(parsed);
              } catch {
                onDraftChange({
                  text: next,
                  error:
                    "JSON の形式を確認してください。修正するまで保存されません。",
                });
              }
            }}
          />
          {draft?.error && (
            <span
              id={`${field.key}-error`}
              role="alert"
              className={css({
                display: "block",
                color: "danger.text",
                fontSize: "sm",
                mt: "1",
              })}
            >
              {draft.error}
            </span>
          )}
        </label>
      );
    }

    default:
      return (
        <label className={css({ display: "block" })}>
          {labelEl}
          <input
            className={input()}
            value={strVal}
            onChange={(e) => onChange(e.target.value)}
          />
        </label>
      );
  }
}
