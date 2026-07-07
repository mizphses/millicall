/**
 * ユーザー管理ページ（Phase 6 T9b）。管理者専用。
 *
 * - ユーザー一覧テーブル（username / display_name / role / origin / enabled / 2FA / email）
 * - 新規作成（SlidePanel フォーム）
 * - 編集（role / display_name / email / enabled）
 * - パスワードリセット（origin=local のみ）
 * - 削除（ConfirmDialog）
 * - 400 (last-admin) / 409 (dup) を toast でサーフェス
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Plus, Pencil, Trash2, KeyRound, ShieldCheck, ShieldOff } from "lucide-react";

import { css, cx } from "styled-system/css";
import { badge, button, input, panel } from "styled-system/recipes";

import { api } from "../api/client";
import { USERS_KEY } from "../queryKeys";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useToast } from "../toast/ToastProvider";
import {
  buildCreatePayload,
  buildPatchPayload,
  canResetPassword,
  editFormFromUser,
  emptyCreateForm,
  hasCreateErrors,
  ORIGIN_LABEL,
  ROLE_LABEL,
  ROLES,
  validateCreateForm,
  type UserCreateForm,
  type UserEditForm,
  type UserFormErrors,
  type UserRead,
} from "./users/formPayload";

// ─────────────────────────────────────────────────────────
// API 関数
// ─────────────────────────────────────────────────────────

async function fetchUsers(): Promise<UserRead[]> {
  const { data, error } = await api.GET("/api/users");
  if (error) throw new Error("ユーザー一覧の取得に失敗しました");
  return (data ?? []) as UserRead[];
}

async function createUser(form: UserCreateForm): Promise<UserRead> {
  const { data, error, response } = await api.POST("/api/users", {
    body: buildCreatePayload(form),
  });
  if (response.status === 409) throw Object.assign(new Error("このユーザー名は既に使用されています"), { status: 409 });
  if (error || !data) throw new Error("ユーザーの作成に失敗しました");
  return data as UserRead;
}

async function patchUser(id: number, form: UserEditForm, original: UserRead): Promise<UserRead> {
  const patch = buildPatchPayload(form, original);
  const { data, error, response } = await api.PATCH("/api/users/{user_id}", {
    params: { path: { user_id: id } },
    body: patch,
  });
  if (response.status === 400) {
    const body = await response.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "最後の管理者は変更できません");
  }
  if (error || !data) throw new Error("ユーザーの更新に失敗しました");
  return data as UserRead;
}

async function resetPassword(id: number, newPassword: string): Promise<void> {
  const { error, response } = await api.POST("/api/users/{user_id}/reset-password", {
    params: { path: { user_id: id } },
    body: { new_password: newPassword },
  });
  if (response.status === 400) {
    const body = await response.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "パスワードリセットに失敗しました");
  }
  if (error) throw new Error("パスワードリセットに失敗しました");
}

async function deleteUser(id: number): Promise<void> {
  const { error, response } = await api.DELETE("/api/users/{user_id}", {
    params: { path: { user_id: id } },
  });
  if (response.status === 400) {
    const body = await response.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "最後の管理者は削除できません");
  }
  if (error) throw new Error("ユーザーの削除に失敗しました");
}

// ─────────────────────────────────────────────────────────
// ページコンポーネント
// ─────────────────────────────────────────────────────────

type PanelMode =
  | { kind: "closed" }
  | { kind: "create" }
  | { kind: "edit"; user: UserRead }
  | { kind: "reset-password"; user: UserRead };

export function UsersPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const listQuery = useQuery({ queryKey: USERS_KEY, queryFn: fetchUsers });

  // ─── パネル / ダイアログ状態 ───
  const [panelMode, setPanelMode] = useState<PanelMode>({ kind: "closed" });
  const [deleteTarget, setDeleteTarget] = useState<UserRead | null>(null);

  // ─── 作成フォーム ───
  const [createForm, setCreateForm] = useState<UserCreateForm>(emptyCreateForm());
  const [createErrors, setCreateErrors] = useState<UserFormErrors>({});

  // ─── 編集フォーム ───
  const [editForm, setEditForm] = useState<UserEditForm>({
    display_name: "",
    role: "user",
    email: "",
    enabled: true,
  });

  // ─── パスワードリセットフォーム ───
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  // ─── ミューテーション ───

  const createMutation = useMutation({
    mutationFn: () => createUser(createForm),
    onSuccess: (user) => {
      queryClient.setQueryData(USERS_KEY, (prev: UserRead[] | undefined) =>
        [...(prev ?? []), user]
      );
      toast.success(`ユーザー「${user.username}」を作成しました`);
      setPanelMode({ kind: "closed" });
      setCreateForm(emptyCreateForm());
      setCreateErrors({});
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "作成に失敗しました");
    },
  });

  const editMutation = useMutation({
    mutationFn: () => {
      if (panelMode.kind !== "edit") throw new Error();
      return patchUser(panelMode.user.id, editForm, panelMode.user);
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(USERS_KEY, (prev: UserRead[] | undefined) =>
        (prev ?? []).map((u) => (u.id === updated.id ? updated : u))
      );
      toast.success("ユーザー情報を更新しました");
      setPanelMode({ kind: "closed" });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "更新に失敗しました");
    },
  });

  const resetMutation = useMutation({
    mutationFn: () => {
      if (panelMode.kind !== "reset-password") throw new Error();
      return resetPassword(panelMode.user.id, newPassword);
    },
    onSuccess: () => {
      toast.success("パスワードをリセットしました");
      setPanelMode({ kind: "closed" });
      setNewPassword("");
      setConfirmPassword("");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "パスワードリセットに失敗しました");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => {
      if (!deleteTarget) throw new Error();
      return deleteUser(deleteTarget.id);
    },
    onSuccess: () => {
      queryClient.setQueryData(USERS_KEY, (prev: UserRead[] | undefined) =>
        (prev ?? []).filter((u) => u.id !== deleteTarget?.id)
      );
      toast.success(`ユーザーを削除しました`);
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "削除に失敗しました");
      setDeleteTarget(null);
    },
  });

  // ─── テーブルカラム定義 ───
  const columns: Column<UserRead>[] = [
    {
      key: "username",
      header: "ユーザー名",
      render: (u) => (
        <span className={css({ fontWeight: "500", fontFamily: "mono", fontSize: "sm" })}>
          {u.username}
        </span>
      ),
    },
    {
      key: "display_name",
      header: "表示名",
    },
    {
      key: "role",
      header: "ロール",
      render: (u) => (
        <span
          className={badge({
            tone: u.role === "admin" ? "accent" : "neutral",
          })}
        >
          {ROLE_LABEL[u.role] ?? u.role}
        </span>
      ),
    },
    {
      key: "origin",
      header: "連携",
      render: (u) => (
        <span className={badge({ tone: u.origin === "local" ? "neutral" : "success" })}>
          {ORIGIN_LABEL[u.origin] ?? u.origin}
        </span>
      ),
    },
    {
      key: "enabled",
      header: "有効",
      render: (u) => (
        <span className={badge({ tone: u.enabled ? "success" : "warn" })}>
          {u.enabled ? "有効" : "無効"}
        </span>
      ),
    },
    {
      key: "totp_enabled",
      header: "2FA",
      render: (u) =>
        u.totp_enabled ? (
          <ShieldCheck size={16} className={css({ color: "success.text", display: "inline" })} />
        ) : (
          <ShieldOff size={16} className={css({ color: "text.subtle", display: "inline" })} />
        ),
    },
    {
      key: "email",
      header: "メール",
      render: (u) => (
        <span className={css({ fontSize: "sm", color: "text.muted" })}>{u.email ?? "—"}</span>
      ),
    },
    {
      key: "_actions",
      header: "",
      width: "180px",
      align: "right",
      render: (u) => (
        <div className={css({ display: "flex", gap: "1", justifyContent: "flex-end" })}>
          {/* パスワードリセット（local のみ） */}
          {canResetPassword(u) && (
            <button
              type="button"
              className={button({ variant: "ghost", size: "sm" })}
              title="パスワードをリセット"
              onClick={() => {
                setNewPassword("");
                setConfirmPassword("");
                setPanelMode({ kind: "reset-password", user: u });
              }}
            >
              <KeyRound size={14} />
            </button>
          )}
          {/* 編集 */}
          <button
            type="button"
            className={button({ variant: "ghost", size: "sm" })}
            title="編集"
            onClick={() => {
              setEditForm(editFormFromUser(u));
              setPanelMode({ kind: "edit", user: u });
            }}
          >
            <Pencil size={14} />
          </button>
          {/* 削除 */}
          <button
            type="button"
            className={button({ variant: "ghost", size: "sm" })}
            title="削除"
            onClick={() => setDeleteTarget(u)}
          >
            <Trash2 size={14} className={css({ color: "danger.text" })} />
          </button>
        </div>
      ),
    },
  ];

  // ─── パスワードリセットの入力バリデーション ───
  const resetPasswordError =
    newPassword && confirmPassword && newPassword !== confirmPassword
      ? "パスワードが一致しません"
      : newPassword && newPassword.length < 8
      ? "パスワードは 8 文字以上必要です"
      : null;

  return (
    <PageLayout title="ユーザー管理" description="システムユーザーの一覧・作成・編集・削除">
      {/* ヘッダアクション */}
      <div className={css({ display: "flex", justifyContent: "flex-end", mb: "4" })}>
        <button
          type="button"
          className={button({ variant: "primary" })}
          style={{ height: "36px" }}
          onClick={() => {
            setCreateForm(emptyCreateForm());
            setCreateErrors({});
            setPanelMode({ kind: "create" });
          }}
        >
          <Plus size={16} />
          ユーザーを追加
        </button>
      </div>

      <DataTable
        columns={columns}
        rows={listQuery.data ?? []}
        rowKey={(u) => u.id}
        loading={listQuery.isLoading}
        emptyMessage="ユーザーがいません"
      />

      {/* ─── 作成パネル ─── */}
      <SlidePanel
        open={panelMode.kind === "create"}
        title="ユーザーを追加"
        onClose={() => setPanelMode({ kind: "closed" })}
        footer={
          <>
            <button
              type="button"
              className={button({ variant: "secondary" })}
              style={{ height: "36px" }}
              onClick={() => setPanelMode({ kind: "closed" })}
              disabled={createMutation.isPending}
            >
              キャンセル
            </button>
            <button
              type="button"
              className={button({ variant: "primary" })}
              style={{ height: "36px" }}
              disabled={createMutation.isPending}
              onClick={() => {
                const errors = validateCreateForm(createForm);
                setCreateErrors(errors);
                if (hasCreateErrors(errors)) return;
                createMutation.mutate();
              }}
            >
              {createMutation.isPending ? "作成中…" : "作成"}
            </button>
          </>
        }
      >
        <div className={css({ display: "flex", flexDirection: "column", gap: "4" })}>
          <Field label="ユーザー名 *" error={createErrors.username}>
            <input
              className={input()}
              value={createForm.username}
              onChange={(e) => setCreateForm((f) => ({ ...f, username: e.target.value }))}
              autoComplete="off"
            />
          </Field>
          <Field label="表示名 *" error={createErrors.display_name}>
            <input
              className={input()}
              value={createForm.display_name}
              onChange={(e) => setCreateForm((f) => ({ ...f, display_name: e.target.value }))}
            />
          </Field>
          <Field label="パスワード *" error={createErrors.password}>
            <input
              className={input()}
              type="password"
              value={createForm.password}
              onChange={(e) => setCreateForm((f) => ({ ...f, password: e.target.value }))}
              autoComplete="new-password"
            />
          </Field>
          <Field label="ロール *" error={createErrors.role}>
            <select
              className={input()}
              value={createForm.role}
              onChange={(e) => setCreateForm((f) => ({ ...f, role: e.target.value }))}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="メールアドレス（任意）" error={createErrors.email}>
            <input
              className={input()}
              type="email"
              value={createForm.email}
              onChange={(e) => setCreateForm((f) => ({ ...f, email: e.target.value }))}
            />
          </Field>
        </div>
      </SlidePanel>

      {/* ─── 編集パネル ─── */}
      <SlidePanel
        open={panelMode.kind === "edit"}
        title={panelMode.kind === "edit" ? `編集: ${panelMode.user.username}` : "編集"}
        onClose={() => setPanelMode({ kind: "closed" })}
        footer={
          <>
            <button
              type="button"
              className={button({ variant: "secondary" })}
              style={{ height: "36px" }}
              onClick={() => setPanelMode({ kind: "closed" })}
              disabled={editMutation.isPending}
            >
              キャンセル
            </button>
            <button
              type="button"
              className={button({ variant: "primary" })}
              style={{ height: "36px" }}
              disabled={editMutation.isPending}
              onClick={() => editMutation.mutate()}
            >
              {editMutation.isPending ? "保存中…" : "保存"}
            </button>
          </>
        }
      >
        <div className={css({ display: "flex", flexDirection: "column", gap: "4" })}>
          <Field label="表示名">
            <input
              className={input()}
              value={editForm.display_name}
              onChange={(e) => setEditForm((f) => ({ ...f, display_name: e.target.value }))}
            />
          </Field>
          <Field label="ロール">
            <select
              className={input()}
              value={editForm.role}
              onChange={(e) => setEditForm((f) => ({ ...f, role: e.target.value }))}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="メールアドレス">
            <input
              className={input()}
              type="email"
              value={editForm.email}
              onChange={(e) => setEditForm((f) => ({ ...f, email: e.target.value }))}
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
              checked={editForm.enabled}
              onChange={(e) => setEditForm((f) => ({ ...f, enabled: e.target.checked }))}
            />
            アカウントを有効にする
          </label>
          {panelMode.kind === "edit" && panelMode.user.origin !== "local" && (
            <div
              className={cx(
                panel(),
                css({ p: "3", fontSize: "sm", color: "text.muted" })
              )}
            >
              このユーザーは {ORIGIN_LABEL[panelMode.user.origin] ?? panelMode.user.origin}{" "}
              連携のため、パスワードリセットはできません。
            </div>
          )}
        </div>
      </SlidePanel>

      {/* ─── パスワードリセットパネル ─── */}
      <SlidePanel
        open={panelMode.kind === "reset-password"}
        title={
          panelMode.kind === "reset-password"
            ? `パスワードリセット: ${panelMode.user.username}`
            : "パスワードリセット"
        }
        onClose={() => setPanelMode({ kind: "closed" })}
        footer={
          <>
            <button
              type="button"
              className={button({ variant: "secondary" })}
              style={{ height: "36px" }}
              onClick={() => setPanelMode({ kind: "closed" })}
              disabled={resetMutation.isPending}
            >
              キャンセル
            </button>
            <button
              type="button"
              className={button({ variant: "primary" })}
              style={{ height: "36px" }}
              disabled={
                resetMutation.isPending ||
                !newPassword ||
                newPassword !== confirmPassword ||
                !!resetPasswordError
              }
              onClick={() => resetMutation.mutate()}
            >
              {resetMutation.isPending ? "リセット中…" : "リセット"}
            </button>
          </>
        }
      >
        <div className={css({ display: "flex", flexDirection: "column", gap: "4" })}>
          <Field label="新しいパスワード">
            <input
              className={input()}
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
            />
          </Field>
          <Field label="パスワードの確認" error={resetPasswordError ?? undefined}>
            <input
              className={input()}
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
            />
          </Field>
        </div>
      </SlidePanel>

      {/* ─── 削除確認 ─── */}
      <ConfirmDialog
        open={!!deleteTarget}
        title="ユーザーを削除"
        message={
          deleteTarget
            ? `「${deleteTarget.username}」を削除します。この操作は取り消せません。`
            : ""
        }
        confirmLabel="削除"
        destructive
        busy={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
        onCancel={() => setDeleteTarget(null)}
      />
    </PageLayout>
  );
}

// ─────────────────────────────────────────────────────────
// 補助コンポーネント
// ─────────────────────────────────────────────────────────

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
        <p className={css({ fontSize: "sm", color: "danger.text", mt: "1" })}>{error}</p>
      ) : null}
    </div>
  );
}
