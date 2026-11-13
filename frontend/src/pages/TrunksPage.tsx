import { ObjectLink, NumberPlanLink } from "../components/ObjectLink";
import { useObjectSelection } from "../hooks/useObjectSelection";
import { Field } from "../components/Field";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Pencil, Trash2 } from "lucide-react";

import { css, cx } from "styled-system/css";
import { badge, button, input } from "styled-system/recipes";

import { api } from "../api/client";
import type { components } from "../api/schema";
import { TRUNKS_KEY, NUMBER_PLAN_KEY } from "../queryKeys";
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
import { numberPlanKindLabel } from "./routes/formPayload";

const checkboxRow = css({
  display: "flex",
  alignItems: "center",
  gap: "2",
  fontSize: "md",
  cursor: "pointer",
});

type NumberPlanEntryRead = components["schemas"]["NumberPlanEntryRead"];

/** トランク名の重複（409）を型で区別するためのエラー。フォームのインライン表示に使う。 */
class TrunkNameConflictError extends Error {}

async function fetchTrunks(): Promise<TrunkRead[]> {
  const { data, error } = await api.GET("/api/trunks");
  if (error) throw new Error("トランク一覧の取得に失敗しました");
  return data ?? [];
}

async function fetchNumberPlan(): Promise<NumberPlanEntryRead[]> {
  const { data, error } = await api.GET("/api/number-plan");
  if (error) throw new Error("番号プランの取得に失敗しました");
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

async function deleteTrunk(id: number): Promise<boolean> {
  const { error, response } = await api.DELETE("/api/trunks/{trunk_id}", {
    params: { path: { trunk_id: id } },
  });
  if (error) throw new Error("トランクの削除に失敗しました");
  return response.headers.has("X-Apply-Warning");
}

type TrunkStatusResult = components["schemas"]["TrunkStatusResult"];

async function fetchTrunkStatus(id: number): Promise<TrunkStatusResult> {
  const { data, error } = await api.GET("/api/trunks/{trunk_id}/status", {
    params: { path: { trunk_id: id } },
  });
  if (error || !data) throw new Error("登録状態の取得に失敗しました");
  return data;
}

/**
 * sofia ゲートウェイの REGISTER 状態バッジ。
 * トランク保存時にバックエンドが即 REGISTER を試行するため、
 * 10 秒間隔のポーリングで結果(REGED / FAIL_WAIT 等)を反映する。
 */
function TrunkStatusBadge({ trunk }: { trunk: TrunkRead }) {
  const statusQuery = useQuery({
    queryKey: [...TRUNKS_KEY, trunk.id, "status"],
    queryFn: () => fetchTrunkStatus(trunk.id),
    enabled: trunk.enabled,
    refetchInterval: 10_000,
  });
  if (!trunk.enabled) {
    return <span className={badge({ tone: "neutral" })}>—</span>;
  }
  const st = statusQuery.data;
  if (!st) {
    return <span className={badge({ tone: "neutral" })}>確認中…</span>;
  }
  if (st.registered) {
    return <span className={badge({ tone: "success" })}>登録済み</span>;
  }
  if (st.state === "TRYING") {
    return <span className={badge({ tone: "warn" })}>登録中…</span>;
  }
  if (st.state === "UNKNOWN") {
    return <span className={badge({ tone: "neutral" })}>不明</span>;
  }
  // FAIL_WAIT / UNREGED / NOT_LOADED など。詳細は title で確認できる。
  return (
    <span className={badge({ tone: "warn" })} title={st.state}>
      未登録
    </span>
  );
}

export function TrunksPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const listQuery = useQuery({ queryKey: TRUNKS_KEY, queryFn: fetchTrunks });
  // 着信先内線番号 select の選択肢（統一番号プラン）。
  const numberPlanQuery = useQuery({ queryKey: NUMBER_PLAN_KEY, queryFn: fetchNumberPlan });
  const numberPlan = numberPlanQuery.data ?? [];
  // FAX 自動検知の切替先候補（番号プランの FAX 受信番号）。
  const faxNumbers = numberPlan.filter((entry) => entry.kind === "fax");

  const [editing, setEditing] = useState<TrunkRead | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [form, setForm] = useState<TrunkFormValues>(emptyForm());
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof TrunkFormValues, string>>>(
    {},
  );
  const [showPassword, setShowPassword] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<TrunkRead | null>(null);

  const selection = useObjectSelection(listQuery.data, openEdit, closePanel);

  function openCreate() {
    selection.clear();
    setEditing(null);
    setForm(emptyForm());
    setFieldErrors({});
    setShowPassword(false);
    setPanelOpen(true);
  }

  function openEdit(trunk: TrunkRead) {
    selection.select(trunk.id);
    setEditing(trunk);
    setForm(formFromTrunk(trunk));
    setFieldErrors({});
    setShowPassword(false);
    setPanelOpen(true);
  }

  function closePanel() {
    setPanelOpen(false);
    selection.clear();
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (editing) return updateTrunk(editing.id, form, editing);
      return createTrunk(form);
    },
    onSuccess: (saved) => {
      if (saved.apply_warning) toast.warn(saved.apply_warning);
      queryClient.invalidateQueries({ queryKey: TRUNKS_KEY });
      toast.success(editing ? "トランクを保存し、反映を試行しました" : "トランクを作成しました");
      setPanelOpen(false);
    selection.clear();
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
    onSuccess: (warning) => {
      if (warning) toast.warn("削除は保存されましたが、電話サービスへの反映に失敗しました。システム画面で電話サービスの状態を確認してください。");
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
      key: "inbound_extension",
      header: "着信先",
      width: "90px",
      render: (row) => { const entry = numberPlan.find(value => value.number === row.inbound_extension); return entry ? <NumberPlanLink entry={entry} /> : row.inbound_extension || "—"; },
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
      key: "registration",
      header: "登録状態",
      width: "110px",
      render: (row) => <TrunkStatusBadge trunk={row} />,
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
        error={listQuery.error}
        onRetry={() => void listQuery.refetch()}
        searchable
        emptyMessage="トランクがまだありません。右上の「トランクを追加」から登録してください。"
      />

      <SlidePanel
        busy={saveMutation.isPending}
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
          {editing && <p className={css({ fontSize: "sm", color: "text.muted" })}>保存すると、変更がない場合も電話サービスへ設定を再反映します。</p>}
          <Field required label="名前（識別子）" error={fieldErrors.name}>
            <input
              className={input({ invalid: fieldErrors.name ? true : undefined })}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              disabled={editing !== null}
              placeholder="my-trunk"
              autoFocus={editing === null}
            />
          </Field>

          <Field required label="表示名" error={fieldErrors.display_name}>
            <input
              className={input({ invalid: fieldErrors.display_name ? true : undefined })}
              value={form.display_name}
              onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
              placeholder="本社回線"
            />
          </Field>

          <Field required label="種別" error={fieldErrors.trunk_type}>
            <select
              className={input({ invalid: fieldErrors.trunk_type ? true : undefined })}
              value={form.trunk_type}
              onChange={(e) =>
                setForm((f) => ({ ...f, trunk_type: e.target.value as "hgw" | "sip" }))
              }
            >
              <option value="hgw">HGW（NTT フレッツ光・LAN 内）</option>
              <option value="sip">インターネット SIP（Brastel my050 等）</option>
            </select>
          </Field>

          <Field required label="ホスト名" error={fieldErrors.host}>
            <input
              className={input({ invalid: fieldErrors.host ? true : undefined })}
              value={form.host}
              onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))}
              placeholder={
                form.trunk_type === "sip" ? "softphone.spc.brastel.ne.jp" : "192.168.1.1"
              }
            />
          </Field>

          <Field required label="ユーザー名" error={fieldErrors.username}>
            <input
              className={input({ invalid: fieldErrors.username ? true : undefined })}
              value={form.username}
              onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
              placeholder="trunk-user"
            />
          </Field>

          <Field
            required={!editing}
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

          <Field label="着信先内線番号" error={fieldErrors.inbound_extension} help={<ObjectLink to="/routes" newTab>番号プランを確認・登録</ObjectLink>}>
            <select
              className={input({ invalid: fieldErrors.inbound_extension ? true : undefined })}
              value={form.inbound_extension}
              onChange={(e) => setForm((f) => ({ ...f, inbound_extension: e.target.value }))}
            >
              <option value="">（着信しない）</option>
              {numberPlan.map((entry) => (
                <option key={`${entry.kind}-${entry.id}`} value={entry.number}>
                  {entry.number} — {entry.label}（{numberPlanKindLabel(entry.kind)}）
                </option>
              ))}
            </select>
          </Field>

          <Field label="送信元ポート（任意・空欄で自動）" error={fieldErrors.source_port}>
            <input
              className={input({ invalid: fieldErrors.source_port ? true : undefined })}
              type="number"
              inputMode="numeric"
              min={1024}
              max={65535}
              value={form.source_port}
              onChange={(e) => setForm((f) => ({ ...f, source_port: e.target.value }))}
              placeholder="自動採番（5080 から +2 ずつ）"
            />
          </Field>

          <Field
            label="OPTIONS キープアライブ間隔（秒、0 で無効）"
            error={fieldErrors.options_ping_seconds}
            help="OPTIONS に応答しない事業者（Brastel 050 など）は 0 にしてください"
          >
            <input
              className={input({ invalid: fieldErrors.options_ping_seconds ? true : undefined })}
              type="number"
              inputMode="numeric"
              min={0}
              max={3600}
              value={form.options_ping_seconds}
              onChange={(e) => setForm((f) => ({ ...f, options_ping_seconds: e.target.value }))}
              placeholder="30"
            />
          </Field>

          {form.trunk_type === "sip" && (
            <Field
              label="着信許可 IP 帯（CIDR・改行区切り）"
              error={fieldErrors.inbound_cidrs}
            >
              <textarea
                className={input({ invalid: fieldErrors.inbound_cidrs ? true : undefined })}
                rows={3}
                value={form.inbound_cidrs}
                onChange={(e) => setForm((f) => ({ ...f, inbound_cidrs: e.target.value }))}
                placeholder={"プロバイダの IP 帯を 1 行 1 件\n例: 203.0.113.0/24"}
              />
              <p className={css({ fontSize: "sm", color: "fg.muted", marginTop: "1" })}>
                空欄にすると着信 ACL を掛けません（SIP ポートが開放されます）。
              </p>
            </Field>
          )}

          <fieldset
            className={css({
              display: "flex",
              flexDirection: "column",
              gap: "3",
              borderWidth: "1px",
              borderColor: "border",
              borderRadius: "md",
              p: "3",
            })}
          >
            <legend className={css({ fontSize: "sm", color: "text.muted", px: "1" })}>
              FAX 自動検知（音声・FAX 併用回線）
            </legend>
            <label className={checkboxRow}>
              <input
                type="checkbox"
                checked={form.fax_detect_enabled}
                onChange={(e) => setForm((f) => ({ ...f, fax_detect_enabled: e.target.checked }))}
              />
              FAX の呼出音（CNG）を検知したら FAX 受信に切り替える
            </label>
            {form.fax_detect_enabled && (
              <>
                <Field
                  required
                  label="切替先の FAX 受信番号"
                  error={fieldErrors.fax_route_number}
                  help={<ObjectLink to="/routes" newTab>FAX 受信番号を登録</ObjectLink>}
                >
                  <select
                    className={input({ invalid: fieldErrors.fax_route_number ? true : undefined })}
                    value={form.fax_route_number}
                    onChange={(e) => setForm((f) => ({ ...f, fax_route_number: e.target.value }))}
                  >
                    <option value="">（選択してください）</option>
                    {faxNumbers.map((entry) => (
                      <option key={entry.id} value={entry.number}>
                        {entry.number} — {entry.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="検知時間（秒・5〜120）" error={fieldErrors.fax_detect_timeout_sec}>
                  <input
                    className={input({ invalid: fieldErrors.fax_detect_timeout_sec ? true : undefined })}
                    type="number"
                    inputMode="numeric"
                    min={5}
                    max={120}
                    value={form.fax_detect_timeout_sec}
                    onChange={(e) => setForm((f) => ({ ...f, fax_detect_timeout_sec: e.target.value }))}
                  />
                </Field>
                <label className={checkboxRow}>
                  <input
                    type="checkbox"
                    checked={form.fax_detect_announce}
                    onChange={(e) => setForm((f) => ({ ...f, fax_detect_announce: e.target.checked }))}
                  />
                  検知中に案内音声を再生
                </label>
                <p className={css({ fontSize: "sm", color: "fg.muted" })}>
                  手動送信など CNG を出さない FAX 機は検知できません。専用の FAX 受信番号への着信も併用してください。
                </p>
              </>
            )}
          </fieldset>

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