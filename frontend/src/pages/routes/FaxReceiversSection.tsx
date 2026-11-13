import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Pencil, Trash2 } from "lucide-react";

import { css } from "styled-system/css";
import { badge, button, input } from "styled-system/recipes";

import { api } from "../../api/client";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { DataTable, type Column } from "../../components/DataTable";
import { Field } from "../../components/Field";
import { ObjectLink } from "../../components/ObjectLink";
import { SlidePanel } from "../../components/SlidePanel";
import { FAX_RECEIVERS_KEY, NUMBER_PLAN_KEY } from "../../queryKeys";
import { useToast } from "../../toast/ToastProvider";
import {
  buildFaxReceiverPayload,
  emptyFaxReceiverForm,
  faxReceiverFormFrom,
  validateFaxReceiverForm,
  type FaxReceiverFormValues,
  type FaxReceiverRead,
} from "./formPayload";

/** 番号重複（409）などフォームに出すエラー。 */
class FaxReceiverConflictError extends Error {}

async function fetchFaxReceivers(): Promise<FaxReceiverRead[]> {
  const { data, error } = await api.GET("/api/fax-receivers");
  if (error) throw new Error("FAX 受信番号の取得に失敗しました");
  return data ?? [];
}

async function saveFaxReceiver(
  editing: FaxReceiverRead | null,
  form: FaxReceiverFormValues,
): Promise<FaxReceiverRead> {
  const body = buildFaxReceiverPayload(form);
  const { data, error, response } = editing
    ? await api.PATCH("/api/fax-receivers/{receiver_id}", {
        params: { path: { receiver_id: editing.id } },
        body,
      })
    : await api.POST("/api/fax-receivers", { body });
  if (response.status === 409) {
    throw new FaxReceiverConflictError("この番号は既に使用されています");
  }
  if (error || !data) throw new Error("FAX 受信番号の保存に失敗しました");
  return data;
}

async function deleteFaxReceiver(id: number): Promise<void> {
  const { error, response } = await api.DELETE("/api/fax-receivers/{receiver_id}", {
    params: { path: { receiver_id: id } },
  });
  if (response.status === 409) {
    const detail = (error as { detail?: string } | undefined)?.detail;
    throw new Error(detail ?? "トランクの FAX 自動検知で使用中のため削除できません");
  }
  if (error) throw new Error("FAX 受信番号の削除に失敗しました");
}

/** ルーティング画面の「FAX 受信」セクション（受信番号の一覧と CRUD）。 */
export function FaxReceiversSection() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: FAX_RECEIVERS_KEY, queryFn: fetchFaxReceivers });

  const [panelOpen, setPanelOpen] = useState(false);
  const [editing, setEditing] = useState<FaxReceiverRead | null>(null);
  const [form, setForm] = useState<FaxReceiverFormValues>(emptyFaxReceiverForm());
  const [fieldErrors, setFieldErrors] = useState<
    Partial<Record<keyof FaxReceiverFormValues, string>>
  >({});
  const [deleteTarget, setDeleteTarget] = useState<FaxReceiverRead | null>(null);

  function openCreate() {
    setEditing(null);
    setForm(emptyFaxReceiverForm());
    setFieldErrors({});
    setPanelOpen(true);
  }

  function openEdit(row: FaxReceiverRead) {
    setEditing(row);
    setForm(faxReceiverFormFrom(row));
    setFieldErrors({});
    setPanelOpen(true);
  }

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: FAX_RECEIVERS_KEY });
    queryClient.invalidateQueries({ queryKey: NUMBER_PLAN_KEY });
  }

  const saveMutation = useMutation({
    mutationFn: () => saveFaxReceiver(editing, form),
    onSuccess: () => {
      invalidate();
      toast.success(editing ? "FAX 受信番号を更新しました" : "FAX 受信番号を追加しました");
      setPanelOpen(false);
    },
    onError: (err) => {
      if (err instanceof FaxReceiverConflictError) {
        setFieldErrors((prev) => ({ ...prev, number: err.message }));
        return;
      }
      toast.error(err instanceof Error ? err.message : "保存に失敗しました");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteFaxReceiver(id),
    onSuccess: () => {
      invalidate();
      toast.success("FAX 受信番号を削除しました");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "削除に失敗しました");
      setDeleteTarget(null);
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errors = validateFaxReceiverForm(form);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    saveMutation.mutate();
  }

  const columns: Column<FaxReceiverRead>[] = [
    { key: "number", header: "番号", width: "110px" },
    { key: "name", header: "名前" },
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
    <section>
      <div
        className={css({
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: "4",
        })}
      >
        <div className={css({ mb: "3" })}>
          <h2 className={css({ fontSize: "lg", fontWeight: "600", color: "text" })}>FAX 受信</h2>
          <p className={css({ fontSize: "sm", color: "text.muted", mt: "1" })}>
            この番号への着信を FAX として受信し PDF で保存します。トランクの着信先や FAX
            自動検知の切替先に指定できます。受信した FAX は
            <ObjectLink to="/faxes">受信 FAX</ObjectLink>で確認できます。
          </p>
        </div>
        <button type="button" className={button({ variant: "primary" })} onClick={openCreate}>
          FAX 受信番号を追加
        </button>
      </div>
      <DataTable
        columns={columns}
        rows={query.data ?? []}
        rowKey={(row) => row.id}
        loading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
        emptyMessage="FAX 受信番号がまだありません。「FAX 受信番号を追加」から登録してください。"
      />

      <SlidePanel
        busy={saveMutation.isPending}
        open={panelOpen}
        title={editing ? "FAX 受信番号を編集" : "FAX 受信番号を追加"}
        onClose={() => setPanelOpen(false)}
        footer={
          <>
            <button
              type="button"
              className={button({ variant: "secondary" })}
              onClick={() => setPanelOpen(false)}
              disabled={saveMutation.isPending}
            >
              キャンセル
            </button>
            <button
              type="submit"
              form="fax-receiver-form"
              className={button({ variant: "primary" })}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? "保存中…" : "保存"}
            </button>
          </>
        }
      >
        <form
          id="fax-receiver-form"
          onSubmit={handleSubmit}
          className={css({ display: "flex", flexDirection: "column", gap: "4" })}
        >
          <Field required label="番号（2〜6 桁）" error={fieldErrors.number}>
            <input
              className={input({ invalid: fieldErrors.number ? true : undefined })}
              value={form.number}
              onChange={(e) => setForm((f) => ({ ...f, number: e.target.value }))}
              placeholder="300"
              autoFocus={editing === null}
            />
          </Field>
          <Field required label="名前" error={fieldErrors.name}>
            <input
              className={input({ invalid: fieldErrors.name ? true : undefined })}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="代表 FAX"
              maxLength={100}
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
        title="FAX 受信番号を削除"
        message={
          deleteTarget
            ? `FAX 受信番号「${deleteTarget.name}」（${deleteTarget.number}）を削除します。受信済みの FAX は残ります。`
            : ""
        }
        confirmLabel="削除"
        destructive
        busy={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />
    </section>
  );
}
