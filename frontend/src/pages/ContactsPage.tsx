import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { css, cx } from "styled-system/css";
import { button, input, panel } from "styled-system/recipes";

import { api } from "../api/client";
import { CONTACTS_KEY, EXTENSIONS_KEY } from "../queryKeys";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useToast } from "../toast/ToastProvider";
import {
  buildCallPayload,
  buildCreatePayload,
  buildUpdatePayload,
  emptyForm,
  formFromContact,
  validateForm,
  type ContactFormValues,
  type ContactRead,
  type ExtensionRead,
} from "./contacts/formPayload";

async function fetchContacts(): Promise<ContactRead[]> {
  const { data, error } = await api.GET("/api/contacts");
  if (error) throw new Error("電話帳の取得に失敗しました");
  return data ?? [];
}

async function fetchExtensions(): Promise<ExtensionRead[]> {
  const { data, error } = await api.GET("/api/extensions");
  if (error) throw new Error("内線一覧の取得に失敗しました");
  return data ?? [];
}

async function createContact(form: ContactFormValues): Promise<ContactRead> {
  const { data, error } = await api.POST("/api/contacts", {
    body: buildCreatePayload(form),
  });
  if (error || !data) throw new Error("連絡先の作成に失敗しました");
  return data;
}

async function updateContact(
  id: number,
  form: ContactFormValues,
  original: ContactRead,
): Promise<ContactRead> {
  const { data, error } = await api.PATCH("/api/contacts/{contact_id}", {
    params: { path: { contact_id: id } },
    body: buildUpdatePayload(form, original),
  });
  if (error || !data) throw new Error("連絡先の更新に失敗しました");
  return data;
}

async function deleteContact(id: number): Promise<void> {
  const { error } = await api.DELETE("/api/contacts/{contact_id}", {
    params: { path: { contact_id: id } },
  });
  if (error) throw new Error("連絡先の削除に失敗しました");
}

export function ContactsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const contactsQuery = useQuery({ queryKey: CONTACTS_KEY, queryFn: fetchContacts });
  const extensionsQuery = useQuery({ queryKey: EXTENSIONS_KEY, queryFn: fetchExtensions });

  const [editing, setEditing] = useState<ContactRead | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [form, setForm] = useState<ContactFormValues>(emptyForm());
  const [fieldErrors, setFieldErrors] = useState<
    Partial<Record<keyof ContactFormValues, string>>
  >({});

  const [deleteTarget, setDeleteTarget] = useState<ContactRead | null>(null);

  // 発信ダイアログ
  const [dialTarget, setDialTarget] = useState<ContactRead | null>(null);
  const [dialExtension, setDialExtension] = useState("");

  const enabledExtensions = useMemo(
    () => (extensionsQuery.data ?? []).filter((ext) => ext.enabled),
    [extensionsQuery.data],
  );

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setFieldErrors({});
    setPanelOpen(true);
  }

  function openEdit(contact: ContactRead) {
    setEditing(contact);
    setForm(formFromContact(contact));
    setFieldErrors({});
    setPanelOpen(true);
  }

  function closePanel() {
    setPanelOpen(false);
  }

  function openDial(contact: ContactRead) {
    setDialTarget(contact);
    setDialExtension("");
  }

  function closeDial() {
    setDialTarget(null);
    setDialExtension("");
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (editing) return updateContact(editing.id, form, editing);
      return createContact(form);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CONTACTS_KEY });
      toast.success(editing ? "連絡先を更新しました" : "連絡先を作成しました");
      setPanelOpen(false);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "保存に失敗しました");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteContact(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CONTACTS_KEY });
      toast.success("連絡先を削除しました");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "削除に失敗しました");
    },
  });

  const dialMutation = useMutation({
    mutationFn: async ({ fromExt, to }: { fromExt: string; to: string }) => {
      const { data, error } = await api.POST("/api/calls", {
        body: buildCallPayload(fromExt, to),
      });
      if (error || !data) throw new Error("発信に失敗しました");
      return data;
    },
    onSuccess: () => {
      toast.success("発信しました");
      closeDial();
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "発信に失敗しました");
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

  const columns: Column<ContactRead>[] = [
    { key: "name", header: "名前" },
    { key: "phone_number", header: "電話番号", width: "160px" },
    { key: "company", header: "会社" },
    { key: "department", header: "部署" },
    {
      key: "actions",
      header: "操作",
      width: "220px",
      align: "right",
      render: (row) => (
        <div className={css({ display: "flex", gap: "2", justifyContent: "flex-end" })}>
          <button
            type="button"
            className={button({ variant: "primary", size: "sm" })}
            onClick={() => openDial(row)}
          >
            発信
          </button>
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            onClick={() => openEdit(row)}
          >
            編集
          </button>
          <button
            type="button"
            className={button({ variant: "ghost", size: "sm" })}
            onClick={() => setDeleteTarget(row)}
          >
            削除
          </button>
        </div>
      ),
    },
  ];

  return (
    <PageLayout
      title="電話帳"
      description="連絡先の管理と発信"
      actions={
        <button type="button" className={button({ variant: "primary" })} onClick={openCreate}>
          連絡先を追加
        </button>
      }
    >
      <DataTable
        columns={columns}
        rows={contactsQuery.data ?? []}
        rowKey={(row) => row.id}
        loading={contactsQuery.isLoading}
        emptyMessage="連絡先がまだありません。右上の「連絡先を追加」から登録してください。"
      />

      <SlidePanel
        open={panelOpen}
        title={editing ? "連絡先を編集" : "連絡先を追加"}
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
              form="contact-form"
              className={button({ variant: "primary" })}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? "保存中…" : "保存"}
            </button>
          </>
        }
      >
        <form
          id="contact-form"
          onSubmit={handleSubmit}
          className={css({ display: "flex", flexDirection: "column", gap: "4" })}
        >
          <Field label="名前" error={fieldErrors.name}>
            <input
              className={input({ invalid: fieldErrors.name ? true : undefined })}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="田中 太郎"
              maxLength={100}
              autoFocus={editing === null}
            />
          </Field>

          <Field label="電話番号" error={fieldErrors.phone_number}>
            <input
              className={input({ invalid: fieldErrors.phone_number ? true : undefined })}
              value={form.phone_number}
              onChange={(e) => setForm((f) => ({ ...f, phone_number: e.target.value }))}
              placeholder="0312345678"
              inputMode="tel"
              maxLength={30}
            />
          </Field>

          <Field label="会社" error={fieldErrors.company}>
            <input
              className={input({ invalid: fieldErrors.company ? true : undefined })}
              value={form.company}
              onChange={(e) => setForm((f) => ({ ...f, company: e.target.value }))}
              placeholder="株式会社サンプル"
              maxLength={100}
            />
          </Field>

          <Field label="部署" error={fieldErrors.department}>
            <input
              className={input({ invalid: fieldErrors.department ? true : undefined })}
              value={form.department}
              onChange={(e) => setForm((f) => ({ ...f, department: e.target.value }))}
              placeholder="営業部"
              maxLength={100}
            />
          </Field>

          <Field label="メモ" error={fieldErrors.notes}>
            <textarea
              className={cx(input(), css({ minH: "textareaMin", resize: "vertical" }))}
              value={form.notes}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
              placeholder="連絡先に関するメモ…"
              maxLength={2000}
            />
          </Field>
        </form>
      </SlidePanel>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="連絡先を削除"
        message={
          deleteTarget
            ? `「${deleteTarget.name}」（${deleteTarget.phone_number}）を削除します。この操作は取り消せません。`
            : ""
        }
        confirmLabel="削除"
        destructive
        busy={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />

      <DialDialog
        open={dialTarget !== null}
        contact={dialTarget}
        extensions={enabledExtensions}
        extension={dialExtension}
        onExtensionChange={setDialExtension}
        onConfirm={() => {
          if (dialTarget && dialExtension) {
            dialMutation.mutate({ fromExt: dialExtension, to: dialTarget.phone_number });
          }
        }}
        onCancel={closeDial}
        busy={dialMutation.isPending}
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

/** 発信ダイアログ。発信元内線を選択して POST /api/calls を呼ぶ。 */
function DialDialog({
  open,
  contact,
  extensions,
  extension,
  onExtensionChange,
  onConfirm,
  onCancel,
  busy,
}: {
  open: boolean;
  contact: ContactRead | null;
  extensions: ExtensionRead[];
  extension: string;
  onExtensionChange: (ext: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
  busy: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open || !contact) return null;

  return (
    <div
      className={css({
        position: "fixed",
        inset: 0,
        zIndex: 950,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        p: "4",
      })}
    >
      <div
        aria-hidden
        onClick={onCancel}
        className={css({ position: "absolute", inset: 0, bg: "gray.900", opacity: 0.2 })}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="発信"
        className={cx(
          panel(),
          css({ position: "relative", w: "dialog", maxW: "100%", p: "5" }),
        )}
      >
        <h2 className={css({ fontSize: "lg", fontWeight: "600", mb: "2" })}>発信</h2>
        <p className={css({ fontSize: "md", color: "text.muted", mb: "4" })}>
          {contact.name}（{contact.phone_number}）へ発信します。
        </p>
        <Field label="発信元内線">
          <select
            className={input()}
            value={extension}
            onChange={(e) => onExtensionChange(e.target.value)}
          >
            <option value="">-- 内線を選択してください --</option>
            {extensions.map((ext) => (
              <option key={ext.id} value={ext.number}>
                {ext.number}　{ext.display_name}
              </option>
            ))}
          </select>
        </Field>
        <div className={css({ display: "flex", justifyContent: "flex-end", gap: "2", mt: "4" })}>
          <button
            type="button"
            className={button({ variant: "secondary" })}
            onClick={onCancel}
            disabled={busy}
          >
            キャンセル
          </button>
          <button
            type="button"
            className={button({ variant: "primary" })}
            onClick={onConfirm}
            disabled={busy || extension === ""}
          >
            {busy ? "発信中…" : "発信"}
          </button>
        </div>
      </div>
    </div>
  );
}
