import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Trash2 } from "lucide-react";

import { css, cx } from "styled-system/css";
import { badge, button, input } from "styled-system/recipes";

import { api } from "../api/client";
import { AGENTS_KEY, PROVIDERS_KEY } from "../queryKeys";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useToast } from "../toast/ToastProvider";
import {
  buildCreatePayload,
  buildUpdatePayload,
  emptyForm,
  formFromAgent,
  validateForm,
  type AiAgentFormValues,
  type AiAgentRead,
  type ProviderRead,
} from "./ai-agents/formPayload";

async function fetchAgents(): Promise<AiAgentRead[]> {
  const { data, error } = await api.GET("/api/ai-agents");
  if (error) throw new Error("AI エージェント一覧の取得に失敗しました");
  return data ?? [];
}

async function fetchProviders(): Promise<ProviderRead[]> {
  const { data, error } = await api.GET("/api/providers");
  if (error) throw new Error("プロバイダ一覧の取得に失敗しました");
  return data ?? [];
}

/** 内線番号の重複（409）を型で区別するためのエラー。フォームのインライン表示に使う。 */
class AgentNumberConflictError extends Error {}

async function createAgent(form: AiAgentFormValues): Promise<AiAgentRead> {
  const { data, error, response } = await api.POST("/api/ai-agents", {
    body: buildCreatePayload(form),
  });
  if (response.status === 409) {
    throw new AgentNumberConflictError("この番号は既に使用されています");
  }
  if (error || !data) throw new Error("AI エージェントの作成に失敗しました");
  return data;
}

async function updateAgent(
  id: number,
  form: AiAgentFormValues,
  original: AiAgentRead,
): Promise<AiAgentRead> {
  const { data, error, response } = await api.PATCH("/api/ai-agents/{agent_id}", {
    params: { path: { agent_id: id } },
    body: buildUpdatePayload(form, original),
  });
  if (response.status === 409) {
    throw new AgentNumberConflictError("この番号は既に使用されています");
  }
  if (error || !data) throw new Error("AI エージェントの更新に失敗しました");
  return data;
}

async function deleteAgent(id: number): Promise<void> {
  const { error } = await api.DELETE("/api/ai-agents/{agent_id}", {
    params: { path: { agent_id: id } },
  });
  if (error) throw new Error("AI エージェントの削除に失敗しました");
}

export function AiAgentsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const agentsQuery = useQuery({ queryKey: AGENTS_KEY, queryFn: fetchAgents });
  const providersQuery = useQuery({ queryKey: PROVIDERS_KEY, queryFn: fetchProviders });

  const [editing, setEditing] = useState<AiAgentRead | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [form, setForm] = useState<AiAgentFormValues>(emptyForm());
  const [fieldErrors, setFieldErrors] = useState<
    Partial<Record<keyof AiAgentFormValues, string>>
  >({});

  const [deleteTarget, setDeleteTarget] = useState<AiAgentRead | null>(null);

  // プロバイダを ID → Read で引けるマップ（テーブルの名前表示用）。
  const providerMap = useMemo(() => {
    const map = new Map<number, ProviderRead>();
    for (const p of providersQuery.data ?? []) map.set(p.id, p);
    return map;
  }, [providersQuery.data]);

  // type ごとにフィルタしたドロップダウン用リスト。
  const llmProviders = useMemo(
    () => (providersQuery.data ?? []).filter((p) => p.type === "llm"),
    [providersQuery.data],
  );
  const ttsProviders = useMemo(
    () => (providersQuery.data ?? []).filter((p) => p.type === "tts"),
    [providersQuery.data],
  );
  const sttProviders = useMemo(
    () => (providersQuery.data ?? []).filter((p) => p.type === "stt"),
    [providersQuery.data],
  );

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setFieldErrors({});
    setPanelOpen(true);
  }

  function openEdit(agent: AiAgentRead) {
    setEditing(agent);
    setForm(formFromAgent(agent));
    setFieldErrors({});
    setPanelOpen(true);
  }

  function closePanel() {
    setPanelOpen(false);
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (editing) return updateAgent(editing.id, form, editing);
      return createAgent(form);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AGENTS_KEY });
      toast.success(editing ? "AI エージェントを更新しました" : "AI エージェントを作成しました");
      setPanelOpen(false);
    },
    onError: (err) => {
      if (err instanceof AgentNumberConflictError) {
        setFieldErrors((prev) => ({ ...prev, number: err.message }));
        return;
      }
      toast.error(err instanceof Error ? err.message : "保存に失敗しました");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteAgent(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AGENTS_KEY });
      toast.success("AI エージェントを削除しました");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "削除に失敗しました");
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errors = validateForm(form, editing ? "edit" : "create");
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    if (editing) {
      const payload = buildUpdatePayload(form, editing);
      if (Object.keys(payload).length === 0) {
        toast.show("変更はありません", "neutral");
        setPanelOpen(false);
        return;
      }
    }
    saveMutation.mutate();
  }

  const columns: Column<AiAgentRead>[] = [
    { key: "name", header: "名前" },
    {
      key: "number",
      header: "番号",
      width: "90px",
      render: (row) => (row.number ? row.number : "—"),
    },
    {
      key: "enabled",
      header: "状態",
      width: "100px",
      render: (row) =>
        row.enabled ? (
          <span className={badge({ tone: "success" })}>有効</span>
        ) : (
          <span className={badge({ tone: "neutral" })}>無効</span>
        ),
    },
    {
      key: "llm_provider_id",
      header: "LLM",
      render: (row) => providerMap.get(row.llm_provider_id)?.name ?? `ID:${row.llm_provider_id}`,
    },
    {
      key: "tts_provider_id",
      header: "TTS",
      render: (row) => providerMap.get(row.tts_provider_id)?.name ?? `ID:${row.tts_provider_id}`,
    },
    {
      key: "stt_provider_id",
      header: "STT",
      render: (row) => providerMap.get(row.stt_provider_id)?.name ?? `ID:${row.stt_provider_id}`,
    },
    {
      key: "actions",
      header: "操作",
      width: "160px",
      align: "right",
      render: (row) => (
        <div className={css({ display: "flex", gap: "2", justifyContent: "flex-end" })}>
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            onClick={() => openEdit(row)}
          >
            <Pencil size={14} />編集
          </button>
          <button
            type="button"
            className={button({ variant: "ghost", size: "sm" })}
            onClick={() => setDeleteTarget(row)}
          >
            <Trash2 size={14} />削除
          </button>
        </div>
      ),
    },
  ];

  return (
    <PageLayout
      title="AI エージェント"
      description="AI 応対エージェントの作成・編集・削除"
      actions={
        <button type="button" className={button({ variant: "primary" })} onClick={openCreate}>
          エージェントを追加
        </button>
      }
    >
      <DataTable
        columns={columns}
        rows={agentsQuery.data ?? []}
        rowKey={(row) => row.id}
        loading={agentsQuery.isLoading}
        emptyMessage="AI エージェントがまだありません。右上の「エージェントを追加」から登録してください。"
      />

      <SlidePanel
        open={panelOpen}
        title={editing ? "AI エージェントを編集" : "AI エージェントを追加"}
        onClose={closePanel}
        footer={
          <>
            <button
              type="button"
              className={button({ variant: "secondary" })}
              onClick={closePanel}
              disabled={saveMutation.isPending}
            >
              キャンセル
            </button>
            <button
              type="submit"
              form="ai-agent-form"
              className={button({ variant: "primary" })}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? "保存中…" : "保存"}
            </button>
          </>
        }
      >
        <form
          id="ai-agent-form"
          onSubmit={handleSubmit}
          className={css({ display: "flex", flexDirection: "column", gap: "4" })}
        >
          <Field label="名前" error={fieldErrors.name}>
            <input
              className={input({ invalid: fieldErrors.name ? true : undefined })}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="受付 AI"
              maxLength={100}
              autoFocus={editing === null}
            />
          </Field>

          <Field label="内線番号（任意・2〜6 桁）" error={fieldErrors.number}>
            <input
              className={input({ invalid: fieldErrors.number ? true : undefined })}
              value={form.number}
              onChange={(e) => setForm((f) => ({ ...f, number: e.target.value }))}
              placeholder="600"
            />
          </Field>

          <Field label="システムプロンプト">
            <textarea
              className={cx(input(), css({ minH: "textareaMin", resize: "vertical" }))}
              value={form.system_prompt}
              onChange={(e) => setForm((f) => ({ ...f, system_prompt: e.target.value }))}
              placeholder="あなたは丁寧な受付 AI です…"
            />
          </Field>

          <Field label="挨拶メッセージ">
            <input
              className={input()}
              value={form.greeting}
              onChange={(e) => setForm((f) => ({ ...f, greeting: e.target.value }))}
              placeholder="お電話ありがとうございます。"
            />
          </Field>

          <Field label="LLM プロバイダ" error={fieldErrors.llm_provider_id}>
            <select
              className={input({ invalid: fieldErrors.llm_provider_id ? true : undefined })}
              value={form.llm_provider_id === null ? "" : String(form.llm_provider_id)}
              onChange={(e) => {
                const val = e.target.value;
                setForm((f) => ({ ...f, llm_provider_id: val === "" ? null : Number(val) }));
              }}
            >
              <option value="">-- 選択してください --</option>
              {llmProviders.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="TTS プロバイダ（音声合成）" error={fieldErrors.tts_provider_id}>
            <select
              className={input({ invalid: fieldErrors.tts_provider_id ? true : undefined })}
              value={form.tts_provider_id === null ? "" : String(form.tts_provider_id)}
              onChange={(e) => {
                const val = e.target.value;
                setForm((f) => ({ ...f, tts_provider_id: val === "" ? null : Number(val) }));
              }}
            >
              <option value="">-- 選択してください --</option>
              {ttsProviders.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="STT プロバイダ（音声認識）" error={fieldErrors.stt_provider_id}>
            <select
              className={input({ invalid: fieldErrors.stt_provider_id ? true : undefined })}
              value={form.stt_provider_id === null ? "" : String(form.stt_provider_id)}
              onChange={(e) => {
                const val = e.target.value;
                setForm((f) => ({ ...f, stt_provider_id: val === "" ? null : Number(val) }));
              }}
            >
              <option value="">-- 選択してください --</option>
              {sttProviders.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="最大履歴（1〜50）" error={fieldErrors.max_history}>
            <input
              type="number"
              className={input({ invalid: fieldErrors.max_history ? true : undefined })}
              value={form.max_history}
              min={1}
              max={50}
              onChange={(e) =>
                setForm((f) => ({ ...f, max_history: Number(e.target.value) }))
              }
            />
          </Field>

          <Field label="無音検知 ms（200〜3000）" error={fieldErrors.silence_end_ms}>
            <input
              type="number"
              className={input({ invalid: fieldErrors.silence_end_ms ? true : undefined })}
              value={form.silence_end_ms}
              min={200}
              max={3000}
              onChange={(e) =>
                setForm((f) => ({ ...f, silence_end_ms: Number(e.target.value) }))
              }
            />
          </Field>

          <label
            className={css({
              display: "flex",
              alignItems: "center",
              gap: "2",
              fontSize: "md",
              cursor: "pointer",
            })}
          >
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
            />
            有効にする
          </label>
        </form>
      </SlidePanel>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="AI エージェントを削除"
        message={
          deleteTarget
            ? `AI エージェント「${deleteTarget.name}」を削除します。この操作は取り消せません。`
            : ""
        }
        confirmLabel="削除"
        destructive
        busy={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />
    </PageLayout>
  );
}

/** フォーム 1 項目（ラベル + 入力 + インラインエラー）。
 * wrap 方式: label > span（テキスト）+ children（input 等）で関連付ける。
 * Task 3-8 が踏襲する標準形。 */
function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className={css({ display: "block" })}>
        <span
          className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}
        >
          {label}
        </span>
        {children}
      </label>
      {error ? (
        <p className={css({ color: "danger.text", fontSize: "sm", mt: "1" })}>{error}</p>
      ) : null}
    </div>
  );
}
