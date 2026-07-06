import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Pencil, Trash2 } from "lucide-react";

import { css, cx } from "styled-system/css";
import { badge, button, input } from "styled-system/recipes";

import { api } from "../api/client";
import { TRUNKS_KEY } from "../queryKeys";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useToast } from "../toast/ToastProvider";
import {
  buildCreatePayload,
  buildUpdatePayload,
  emptyForm,
  formFromTrunk,
  validateForm,
  type TrunkFormValues,
  type TrunkRead,
} from "./trunks/formPayload";

/** トランク名の重複（409）を型で区別するためのエラー。フォームのインライン表示に使う。 */
class TrunkNameConflictError extends Error {}

async function fetchTrunks(): Promise<TrunkRead[]> {
  const { data, error } = await api.GET("/api/trunks");
  if (error) throw new Error("トランク一覧の取得に失敗しました");
  return data ?? [];
}

async function createTrunk(form: TrunkFormValues): Promise<TrunkRead> {
  const { data, error, response } = await api.POST("/api/trunks", {
    body: buildCreatePayload(form),
  });
  if (response.status === 409) {
    throw new TrunkNameConflictError("このトランク名は既に使用されています");
  }
  if (error || !data) throw new Error("トランクの作成に失敗しました");
  return data;
}

async function updateTrunk(
  id: number,
  form: TrunkFormValues,
  original: TrunkRead,
): Promise<TrunkRead> {
  const { data, error } = await api.PATCH("/api/trunks/{trunk_id}", {
    params: { path: { trunk_id: id } },
    body: buildUpdatePayload(form, original),
  });
  if (error || !data) throw new Error("トランクの更新に失敗しました");
  return data;
}

async function deleteTrunk(id: number): Promise<void> {
  const { error } = await api.DELETE("/api/trunks/{trunk_id}", {
    params: { path: { trunk_id: id } },
  });
  if (error) throw new Error("トランクの削除に失敗しました");
}

export function TrunksPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const listQuery = useQuery({ queryKey: TRUNKS_KEY, queryFn: fetchTrunks });

  const [editing, setEditing] = useState<TrunkRead | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [form, setForm] = useState<TrunkFormValues>(emptyForm());
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof TrunkFormValues, string>>>(
    {},
  );
  const [showPassword, setShowPassword] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<TrunkRead | null>(null);

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setFieldErrors({});
    setShowPassword(false);
    setPanelOpen(true);
  }

  function openEdit(trunk: TrunkRead) {
    setEditing(trunk);
    setForm(formFromTrunk(trunk));
    setFieldErrors({});
    setShowPassword(false);
    setPanelOpen(true);
  }

  function closePanel() {
    setPanelOpen(false);
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (editing) return updateTrunk(editing.id, form, editing);
      return createTrunk(form);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: TRUNKS_KEY });
      toast.success(editing ? "トランクを更新しました" : "トランクを作成しました");
      setPanelOpen(false);
    },
    onError: (err) => {
      if (err instanceof TrunkNameConflictError) {
        setFieldErrors((prev) => ({ ...prev, name: err.message }));
        return;
      }
      toast.error(err instanceof Error ? err.message : "保存に失敗しました");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteTrunk(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: TRUNKS_KEY });
      toast.success("トランクを削除しました");
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

  const columns: Column<TrunkRead>[] = [
    { key: "name", header: "名前", width: "150px" },
    { key: "display_name", header: "表示名" },
    { key: "host", header: "ホスト" },
    { key: "username", header: "ユーザー名", width: "120px" },
    {
      key: "has_password",
      header: "パスワード",
      width: "110px",
      render: (row) =>
        row.has_password ? (
          <span className={badge({ tone: "success" })}>設定済み</span>
        ) : (
          <span className={badge({ tone: "warn" })}>未設定</span>
        ),
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
      title="外線トランク"
      description="SIP トランクの管理"
      actions={
        <button type="button" className={button({ variant: "primary" })} onClick={openCreate}>
          トランクを追加
        </button>
      }
    >
      <DataTable
        columns={columns}
        rows={listQuery.data ?? []}
        rowKey={(row) => row.id}
        loading={listQuery.isLoading}
        emptyMessage="トランクがまだありません。右上の「トランクを追加」から登録してください。"
      />

      <SlidePanel
        open={panelOpen}
        title={editing ? "トランクを編集" : "トランクを追加"}
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
              form="trunk-form"
              className={button({ variant: "primary" })}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? "保存中…" : "保存"}
            </button>
          </>
        }
      >
        <form
          id="trunk-form"
          onSubmit={handleSubmit}
          className={css({ display: "flex", flexDirection: "column", gap: "4" })}
        >
          <Field label="名前（識別子）" error={fieldErrors.name}>
            <input
              className={input({ invalid: fieldErrors.name ? true : undefined })}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              disabled={editing !== null}
              placeholder="my-trunk"
              autoFocus={editing === null}
            />
          </Field>

          <Field label="表示名" error={fieldErrors.display_name}>
            <input
              className={input({ invalid: fieldErrors.display_name ? true : undefined })}
              value={form.display_name}
              onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
              placeholder="本社回線"
            />
          </Field>

          <Field label="ホスト名" error={fieldErrors.host}>
            <input
              className={input({ invalid: fieldErrors.host ? true : undefined })}
              value={form.host}
              onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))}
              placeholder="sip.provider.example.com"
            />
          </Field>

          <Field label="ユーザー名" error={fieldErrors.username}>
            <input
              className={input({ invalid: fieldErrors.username ? true : undefined })}
              value={form.username}
              onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
              placeholder="trunk-user"
            />
          </Field>

          <Field
            label={editing ? "パスワード（空のまま＝変更しない）" : "パスワード"}
            error={fieldErrors.password}
          >
            <div className={css({ display: "flex", gap: "2" })}>
              <input
                className={cx(input({ invalid: fieldErrors.password ? true : undefined }), css({ flex: "1" }))}
                type={showPassword ? "text" : "password"}
                value={form.password}
                onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                placeholder={editing ? "変更する場合のみ入力" : "パスワードを入力"}
                autoComplete="new-password"
              />
              <button
                type="button"
                className={button({ variant: "secondary", size: "sm" })}
                onClick={() => setShowPassword((v) => !v)}
              >
                {showPassword ? "隠す" : "表示"}
              </button>
            </div>
          </Field>

          <Field label="DID 番号（省略可）" error={fieldErrors.did_number}>
            <input
              className={input({ invalid: fieldErrors.did_number ? true : undefined })}
              value={form.did_number}
              onChange={(e) => setForm((f) => ({ ...f, did_number: e.target.value }))}
              placeholder="0312345678"
            />
          </Field>

          <Field label="発信者番号（省略可）" error={fieldErrors.caller_id}>
            <input
              className={input({ invalid: fieldErrors.caller_id ? true : undefined })}
              value={form.caller_id}
              onChange={(e) => setForm((f) => ({ ...f, caller_id: e.target.value }))}
              placeholder="0312345678"
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
        title="トランクを削除"
        message={
          deleteTarget
            ? `トランク「${deleteTarget.display_name}」（${deleteTarget.name}）を削除します。この操作は取り消せません。`
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
 * Task 2（ExtensionsPage）で確立した標準形を踏襲。 */
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
