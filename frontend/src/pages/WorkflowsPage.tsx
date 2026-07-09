/**
 * ワークフロー一覧ページ（Phase 4b Task 10）。
 * 一覧・作成・削除・有効/無効切替を担う。編集は WorkflowEditorPage へ遷移。
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { AlertTriangle, Pencil, ToggleLeft, ToggleRight, Trash2 } from "lucide-react";

import { css } from "styled-system/css";
import { badge, button, input } from "styled-system/recipes";

import { api } from "../api/client";
import type { components } from "../api/schema";
import { WORKFLOWS_KEY } from "../queryKeys";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useToast } from "../toast/ToastProvider";

type WorkflowRead = components["schemas"]["WorkflowRead"];
type WorkflowUpsert = components["schemas"]["WorkflowUpsert"];

/** 409 重複ナンバー専用エラー。 */
class NumberConflictError extends Error {}

async function fetchWorkflows(): Promise<WorkflowRead[]> {
  const { data, error } = await api.GET("/api/workflows");
  if (error) throw new Error("ワークフロー一覧の取得に失敗しました");
  return data ?? [];
}

async function createWorkflow(body: WorkflowUpsert): Promise<WorkflowRead> {
  const { data, error, response } = await api.POST("/api/workflows", { body });
  if (response.status === 409) throw new NumberConflictError("この番号は既に使用されています");
  if (error || !data) throw new Error("ワークフローの作成に失敗しました");
  return data;
}

async function updateWorkflow(id: number, body: WorkflowUpsert): Promise<WorkflowRead> {
  const { data, error } = await api.PUT("/api/workflows/{workflow_id}", {
    params: { path: { workflow_id: id } },
    body,
  });
  if (error || !data) throw new Error("ワークフローの更新に失敗しました");
  return data;
}

async function deleteWorkflow(id: number): Promise<void> {
  const { error } = await api.DELETE("/api/workflows/{workflow_id}", {
    params: { path: { workflow_id: id } },
  });
  if (error) throw new Error("ワークフローの削除に失敗しました");
}

interface CreateForm {
  name: string;
  number: string;
  description: string;
}

function emptyForm(): CreateForm {
  return { name: "", number: "", description: "" };
}

function validateForm(form: CreateForm): Partial<Record<keyof CreateForm, string>> {
  const errors: Partial<Record<keyof CreateForm, string>> = {};
  if (!form.name.trim()) errors.name = "名前は必須です";
  if (form.name.trim().length > 100) errors.name = "名前は100文字以内にしてください";
  if (!form.number.trim()) errors.number = "番号は必須です";
  if (!/^\d{2,6}$/.test(form.number.trim()))
    errors.number = "内線番号は2〜6桁の数字で入力してください";
  return errors;
}

export function WorkflowsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const listQuery = useQuery({ queryKey: WORKFLOWS_KEY, queryFn: fetchWorkflows });
  const workflows = listQuery.data ?? [];

  const [panelOpen, setPanelOpen] = useState(false);
  const [form, setForm] = useState<CreateForm>(emptyForm());
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof CreateForm, string>>>({});
  const [deleteTarget, setDeleteTarget] = useState<WorkflowRead | null>(null);

  function openCreate() {
    setForm(emptyForm());
    setFieldErrors({});
    setPanelOpen(true);
  }

  function closePanel() {
    setPanelOpen(false);
  }

  const createMutation = useMutation({
    mutationFn: () =>
      createWorkflow({
        name: form.name.trim(),
        number: form.number.trim(),
        description: form.description.trim(),
        enabled: true,
        // バックエンドは「start ノードちょうど1個」を必須とするため、
        // 空グラフではなく start のみの最小定義で作成する(空だと 422 で作成不能)。
        definition: {
          nodes: [{ id: "start", type: "start", position: { x: 80, y: 80 }, config: {} }],
          edges: [],
        },
      }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: WORKFLOWS_KEY });
      toast.success("ワークフローを作成しました");
      setPanelOpen(false);
      // 作成直後にエディタへ
      void navigate({ to: "/workflows/$workflowId", params: { workflowId: String(created.id) } });
    },
    onError: (err) => {
      if (err instanceof NumberConflictError) {
        setFieldErrors((prev) => ({ ...prev, number: err.message }));
        return;
      }
      toast.error(err instanceof Error ? err.message : "作成に失敗しました");
    },
  });

  const toggleMutation = useMutation({
    mutationFn: (wf: WorkflowRead) =>
      updateWorkflow(wf.id, {
        name: wf.name,
        number: wf.number,
        description: wf.description,
        enabled: !wf.enabled,
        default_tts_provider_id: wf.default_tts_provider_id ?? undefined,
        definition: wf.definition,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: WORKFLOWS_KEY });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "更新に失敗しました");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteWorkflow(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: WORKFLOWS_KEY });
      toast.success("ワークフローを削除しました");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "削除に失敗しました");
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errors = validateForm(form);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    createMutation.mutate();
  }

  const columns: Column<WorkflowRead>[] = [
    { key: "number", header: "内線番号", width: "120px" },
    { key: "name", header: "名前" },
    {
      key: "description",
      header: "説明",
      render: (row) => (
        <span className={css({ color: "text.muted", fontSize: "sm" })}>
          {row.description || "—"}
        </span>
      ),
    },
    {
      key: "warnings",
      header: "警告",
      width: "80px",
      align: "center",
      render: (row) =>
        (row.warnings ?? []).length > 0 ? (
          <span
            title={(row.warnings ?? []).join("\n")}
            className={css({ display: "inline-flex", alignItems: "center", color: "warn.text" })}
          >
            <AlertTriangle size={16} />
          </span>
        ) : null,
    },
    {
      key: "enabled",
      header: "状態",
      width: "90px",
      render: (row) =>
        row.enabled ? (
          <span className={badge({ tone: "success" })}>有効</span>
        ) : (
          <span className={badge({ tone: "neutral" })}>無効</span>
        ),
    },
    {
      key: "actions",
      header: "操作",
      width: "220px",
      align: "right",
      render: (row) => (
        <div className={css({ display: "flex", gap: "2", justifyContent: "flex-end" })}>
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            onClick={() =>
              void navigate({
                to: "/workflows/$workflowId",
                params: { workflowId: String(row.id) },
              })
            }
          >
            <Pencil size={14} />編集
          </button>
          <button
            type="button"
            className={button({ variant: "ghost", size: "sm" })}
            onClick={() => toggleMutation.mutate(row)}
            disabled={toggleMutation.isPending}
            title={row.enabled ? "無効にする" : "有効にする"}
          >
            {row.enabled ? <ToggleRight size={14} /> : <ToggleLeft size={14} />}
            {row.enabled ? "無効化" : "有効化"}
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
      title="ワークフロー"
      description="IVR・AI 応対フローの作成・編集・管理"
      actions={
        <button type="button" className={button({ variant: "primary" })} onClick={openCreate}>
          ワークフローを追加
        </button>
      }
    >
      <DataTable
        columns={columns}
        rows={workflows}
        rowKey={(row) => row.id}
        loading={listQuery.isLoading}
        emptyMessage="ワークフローがまだありません。右上の「ワークフローを追加」から登録してください。"
      />

      <SlidePanel
        open={panelOpen}
        title="ワークフローを追加"
        onClose={closePanel}
        footer={
          <>
            <button
              type="button"
              className={button({ variant: "secondary" })}
              onClick={closePanel}
              disabled={createMutation.isPending}
            >
              キャンセル
            </button>
            <button
              type="submit"
              form="workflow-create-form"
              className={button({ variant: "primary" })}
              disabled={createMutation.isPending}
            >
              {createMutation.isPending ? "作成中…" : "作成してエディタへ"}
            </button>
          </>
        }
      >
        <form
          id="workflow-create-form"
          onSubmit={handleSubmit}
          className={css({ display: "flex", flexDirection: "column", gap: "4" })}
        >
          <Field label="名前" error={fieldErrors.name}>
            <input
              className={input({ invalid: fieldErrors.name ? true : undefined })}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="受付フロー"
              maxLength={100}
              autoFocus
            />
          </Field>

          <Field label="内線番号" error={fieldErrors.number}>
            <input
              className={input({ invalid: fieldErrors.number ? true : undefined })}
              value={form.number}
              onChange={(e) => setForm((f) => ({ ...f, number: e.target.value }))}
              placeholder="0312345678"
              maxLength={30}
            />
          </Field>

          <Field label="説明（任意）">
            <input
              className={input()}
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              placeholder="メインの受付フロー"
            />
          </Field>
        </form>
      </SlidePanel>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="ワークフローを削除"
        message={
          deleteTarget
            ? `ワークフロー「${deleteTarget.name}」を削除します。この操作は取り消せません。`
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
        <span className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}>
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
