import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Check, Copy, Eye, EyeOff, KeyRound, Pencil, Trash2 } from "lucide-react";

import { css } from "styled-system/css";
import { badge, button, input } from "styled-system/recipes";

import type { components } from "../api/schema";

import { api } from "../api/client";
import { EXTENSIONS_KEY } from "../queryKeys";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useToast } from "../toast/ToastProvider";
import {
  buildCreatePayload,
  buildUpdatePayload,
  CALLING_PERMISSION_LABEL,
  CALLING_PERMISSIONS,
  emptyForm,
  formFromExtension,
  toCallingPermission,
  validateForm,
  type ExtensionFormValues,
  type ExtensionRead,
} from "./extensions/formPayload";

/** 内線番号の重複（409）を型で区別するためのエラー。フォームのインライン表示に使う。 */
class NumberConflictError extends Error {}

async function fetchExtensions(): Promise<ExtensionRead[]> {
  const { data, error } = await api.GET("/api/extensions");
  if (error) throw new Error("内線一覧の取得に失敗しました");
  return data ?? [];
}

async function createExtension(form: ExtensionFormValues): Promise<ExtensionRead> {
  const { data, error, response } = await api.POST("/api/extensions", {
    body: buildCreatePayload(form),
  });
  if (response.status === 409) {
    throw new NumberConflictError("この内線番号は既に使用されています");
  }
  if (error || !data) throw new Error("内線の作成に失敗しました");
  return data;
}

async function updateExtension(
  id: number,
  form: ExtensionFormValues,
  original: ExtensionRead,
): Promise<ExtensionRead> {
  const { data, error } = await api.PATCH("/api/extensions/{ext_id}", {
    params: { path: { ext_id: id } },
    body: buildUpdatePayload(form, original),
  });
  if (error || !data) throw new Error("内線の更新に失敗しました");
  return data;
}

async function deleteExtension(id: number): Promise<void> {
  const { error } = await api.DELETE("/api/extensions/{ext_id}", {
    params: { path: { ext_id: id } },
  });
  if (error) throw new Error("内線の削除に失敗しました");
}

type ExtensionCredentials = components["schemas"]["ExtensionCredentials"];

async function fetchCredentials(id: number): Promise<ExtensionCredentials> {
  const { data, error } = await api.GET("/api/extensions/{ext_id}/credentials", {
    params: { path: { ext_id: id } },
  });
  if (error || !data) throw new Error("認証情報の取得に失敗しました");
  return data;
}

export function ExtensionsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const listQuery = useQuery({ queryKey: EXTENSIONS_KEY, queryFn: fetchExtensions });

  // 編集対象（null なら新規作成）と、パネルの開閉。
  const [editing, setEditing] = useState<ExtensionRead | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [form, setForm] = useState<ExtensionFormValues>(emptyForm());
  const [fieldErrors, setFieldErrors] = useState<
    Partial<Record<"number" | "display_name", string>>
  >({});

  const [deleteTarget, setDeleteTarget] = useState<ExtensionRead | null>(null);

  // 認証情報パネル: 対象内線（開いたときだけ取得する）。
  const [credentialsTarget, setCredentialsTarget] = useState<ExtensionRead | null>(null);

  function openCredentials(ext: ExtensionRead) {
    setCredentialsTarget(ext);
  }

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setFieldErrors({});
    setPanelOpen(true);
  }

  function openEdit(ext: ExtensionRead) {
    setEditing(ext);
    setForm(formFromExtension(ext));
    setFieldErrors({});
    setPanelOpen(true);
  }

  function closePanel() {
    setPanelOpen(false);
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (editing) return updateExtension(editing.id, form, editing);
      return createExtension(form);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: EXTENSIONS_KEY });
      toast.success(editing ? "内線を更新しました" : "内線を作成しました");
      setPanelOpen(false);
    },
    onError: (err) => {
      if (err instanceof NumberConflictError) {
        // 409 はフォームのインラインエラーとして表示する（toast にしない）。
        setFieldErrors((prev) => ({ ...prev, number: err.message }));
        return;
      }
      toast.error(err instanceof Error ? err.message : "保存に失敗しました");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteExtension(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: EXTENSIONS_KEY });
      toast.success("内線を削除しました");
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
    // 編集時に変更がなければ PATCH を送らずパネルを閉じる。
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

  const columns: Column<ExtensionRead>[] = [
    { key: "number", header: "番号", width: "140px" },
    { key: "display_name", header: "表示名" },
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
      key: "actions",
      header: "操作",
      width: "260px",
      align: "right",
      render: (row) => (
        <div className={css({ display: "flex", gap: "2", justifyContent: "flex-end" })}>
          <button
            type="button"
            className={button({ variant: "ghost", size: "sm" })}
            onClick={() => openCredentials(row)}
          >
            <KeyRound size={14} />認証情報
          </button>
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
      title="内線"
      description="内線番号の作成・編集・削除"
      actions={
        <button type="button" className={button({ variant: "primary" })} onClick={openCreate}>
          内線を追加
        </button>
      }
    >
      <DataTable
        columns={columns}
        rows={listQuery.data ?? []}
        rowKey={(row) => row.id}
        loading={listQuery.isLoading}
        emptyMessage="内線がまだありません。右上の「内線を追加」から登録してください。"
      />

      <SlidePanel
        open={panelOpen}
        title={editing ? "内線を編集" : "内線を追加"}
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
              form="extension-form"
              className={button({ variant: "primary" })}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? "保存中…" : "保存"}
            </button>
          </>
        }
      >
        <form
          id="extension-form"
          onSubmit={handleSubmit}
          className={css({ display: "flex", flexDirection: "column", gap: "4" })}
        >
          <Field label="内線番号" error={fieldErrors.number}>
            <input
              className={input({ invalid: fieldErrors.number ? true : undefined })}
              value={form.number}
              onChange={(e) => setForm((f) => ({ ...f, number: e.target.value }))}
              // 番号は作成時のみ設定可能（backend の update は番号を受け付けない）。
              disabled={editing !== null}
              inputMode="numeric"
              placeholder="1001"
              autoFocus={editing === null}
            />
          </Field>

          <Field label="表示名" error={fieldErrors.display_name}>
            <input
              className={input({ invalid: fieldErrors.display_name ? true : undefined })}
              value={form.display_name}
              onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
              placeholder="営業部 田中"
            />
          </Field>

          <Field label="発信権限">
            <select
              className={input()}
              value={form.calling_permission}
              onChange={(e) =>
                setForm((f) => ({ ...f, calling_permission: toCallingPermission(e.target.value) }))
              }
            >
              {CALLING_PERMISSIONS.map((p) => (
                <option key={p} value={p}>
                  {CALLING_PERMISSION_LABEL[p]}
                </option>
              ))}
            </select>
          </Field>

          {editing ? (
            <>
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
            </>
          ) : null}
        </form>
      </SlidePanel>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="内線を削除"
        message={
          deleteTarget
            ? `内線 ${deleteTarget.number}（${deleteTarget.display_name}）を削除します。この操作は取り消せません。`
            : ""
        }
        confirmLabel="削除"
        destructive
        busy={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />

      <CredentialsPanel target={credentialsTarget} onClose={() => setCredentialsTarget(null)} />
    </PageLayout>
  );
}

/** SIP 認証情報パネル。開いたときだけ /credentials を取得し、ソフトフォン設定値を表示する。 */
function CredentialsPanel({
  target,
  onClose,
}: {
  target: ExtensionRead | null;
  onClose: () => void;
}) {
  const open = target !== null;
  const credentialsQuery = useQuery({
    queryKey: ["extension-credentials", target?.id],
    queryFn: () => fetchCredentials(target!.id),
    // 開いたときだけ取得する（一覧では取得しない）。
    enabled: open,
    // 平文パスワードのため長期キャッシュしない。
    staleTime: 0,
    gcTime: 0,
  });

  return (
    <SlidePanel
      open={open}
      title={target ? `認証情報（内線 ${target.number}）` : "認証情報"}
      onClose={onClose}
      footer={
        <button type="button" className={button({ variant: "secondary" })} onClick={onClose}>
          閉じる
        </button>
      }
    >
      <div className={css({ display: "flex", flexDirection: "column", gap: "4" })}>
        <p className={css({ fontSize: "sm", color: "text.muted", lineHeight: "1.6" })}>
          Zoiper 等のソフトフォンに以下の値を入力してください。ソフトフォンは電話機と同じ社内LAN（子ネットワーク）上に接続してください。
        </p>

        {credentialsQuery.isLoading ? (
          <p className={css({ fontSize: "sm", color: "text.muted" })}>読み込み中…</p>
        ) : credentialsQuery.isError ? (
          <p className={css({ fontSize: "sm", color: "danger.text" })}>
            認証情報の取得に失敗しました。
          </p>
        ) : credentialsQuery.data ? (
          <CredentialsFields data={credentialsQuery.data} />
        ) : null}
      </div>
    </SlidePanel>
  );
}

function CredentialsFields({ data }: { data: ExtensionCredentials }) {
  const [revealed, setRevealed] = useState(false);

  return (
    <div className={css({ display: "flex", flexDirection: "column", gap: "3" })}>
      <CopyRow label="ユーザー名 / 認証ID" value={data.number} />
      <CopyRow
        label="パスワード"
        value={data.password}
        secret
        revealed={revealed}
        onToggleReveal={() => setRevealed((v) => !v)}
      />
      <CopyRow label="SIP サーバ" value={data.sip_server} />
      <CopyRow label="ポート" value={String(data.sip_port)} />
      <CopyRow label="ドメイン" value={data.domain} />
      <CopyRow label="表示名" value={data.display_name} />
      <CopyRow label="トランスポート" value={data.transport} />
    </div>
  );
}

/** ラベル + 値（伏字/表示トグル対応）+ コピーボタンの 1 行。 */
function CopyRow({
  label,
  value,
  secret = false,
  revealed = false,
  onToggleReveal,
}: {
  label: string;
  value: string;
  secret?: boolean;
  revealed?: boolean;
  onToggleReveal?: () => void;
}) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("コピーに失敗しました");
    }
  }

  const display = secret && !revealed ? "•".repeat(Math.min(value.length, 24)) : value;

  return (
    <div>
      <span
        className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}
      >
        {label}
      </span>
      <div className={css({ display: "flex", gap: "2", alignItems: "center" })}>
        <code
          className={css({
            flex: 1,
            minWidth: 0,
            fontSize: "sm",
            fontFamily: "mono",
            bg: "gray.50",
            borderWidth: "1px",
            borderStyle: "solid",
            borderColor: "border",
            borderRadius: "md",
            px: "3",
            py: "2",
            overflowX: "auto",
            whiteSpace: "nowrap",
            wordBreak: "break-all",
          })}
        >
          {display}
        </code>
        {secret ? (
          <button
            type="button"
            aria-label={revealed ? "パスワードを隠す" : "パスワードを表示"}
            className={button({ variant: "ghost", size: "sm" })}
            onClick={onToggleReveal}
          >
            {revealed ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        ) : null}
        <button
          type="button"
          aria-label={`${label}をコピー`}
          className={button({ variant: "secondary", size: "sm" })}
          onClick={copy}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
      </div>
    </div>
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
