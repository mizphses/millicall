import { useObjectSelection } from "../hooks/useObjectSelection";
import { Field as ProvisionField } from "../components/Field";
/**
 * デバイス管理ページ（Phase 5 Task 6）。
 *
 * セクション:
 *   (a) デバイス一覧テーブル（MAC / IP / ホスト名 / モデル / 内線 / provisioned バッジ / 最終確認）
 *   (b) 「リース同期」ボタン → POST /api/devices/sync（スピナー付き・502 エラー表示）
 *   (c) 「内線割当」/「内線を変更」アクション → SlidePanel で内線を選ぶ（issue #149）
 *       → POST /api/devices/{id}/quick-provision
 *       既定は /extensions で定義済みの内線から選ぶ（extension_id）。別の端末に割当済みの
 *       内線を選ぶと付け替えの確認を挟み reassign: true で送る。「新しい内線を作成して
 *       割り当てる」に切り替えると従来どおり番号 + 表示名を入力する。
 *       「割当を解除」→ PATCH /api/devices/{id}（extension_id: null）
 *   (d) 削除アクション（confirm ダイアログ）→ DELETE /api/devices/{id}
 *   (e) 状態列（issue #85）: 取得待ち / トークン待ち / 取得済み（登録未確認・未登録）/ 完了。
 *       「完了」は設定ファイル取得済み かつ SIP 登録確認済み（GET /api/devices/registrations）
 *       のときだけ表示する（deviceStatus.ts）。
 *   (f) 再同期 → POST /api/devices/{id}/resync（再取得の「指示」。結果をトーストに出す）
 *   (g) 厳格モード切替 → PATCH /api/devices/{id}、トークン発行 → POST /api/devices/{id}/token
 *       （トークン付き URL は一度だけ表示）、トークン破棄 → DELETE /api/devices/{id}/token
 *
 * デザイン原則:
 *   - PandaCSS css()/cx() + styled-system/recipes (panel, button, input, badge)
 *   - lucide-react アイコン（サイズは明示的な px 値）
 *   - ExtensionsPage / NetworkPage のハウススタイルに準拠
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  RefreshCw,
  RotateCw,
  Smartphone,
  Trash2,
  PhoneCall,
  Loader2,
  CheckCircle2,
  Circle,
  KeyRound,
  ShieldCheck,
  ShieldOff,
  Copy,
  Check,
  AlertTriangle,
  Unlink,
} from "lucide-react";

import { css, cx } from "styled-system/css";
import { badge, button, input, panel } from "styled-system/recipes";

import { api } from "../api/client";
import type { components } from "../api/schema.d";
import { DEVICE_REGISTRATIONS_KEY, DEVICES_KEY, EXTENSIONS_KEY } from "../queryKeys";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useToast } from "../toast/ToastProvider";
import { deviceStatus, resyncToast } from "./devices/deviceStatus";
import {
  type AssignErrors,
  type AssignForm,
  type AssignMode,
  type QuickProvisionPayload,
  buildAssignPayload,
  deviceShortLabel,
  emptyAssignForm,
  extensionOptions,
  validateAssignForm,
} from "./devices/extensionAssign";

// ---------------------------------------------------------------------------
// 型エイリアス（schema.d.ts の自動生成型を再利用）
// ---------------------------------------------------------------------------

type DeviceRead = components["schemas"]["DeviceRead"];
type DeviceRegistrations = components["schemas"]["DeviceRegistrations"];
type ProvisionTokenIssued = components["schemas"]["ProvisionTokenIssued"];
type ResyncResultRead = components["schemas"]["ResyncResultRead"];
type ProvisionMode = DeviceRead["provision_mode"];
type ExtensionRead = components["schemas"]["ExtensionRead"];

// ---------------------------------------------------------------------------
// API 関数
// ---------------------------------------------------------------------------

async function fetchDevices(): Promise<DeviceRead[]> {
  const { data, error } = await api.GET("/api/devices");
  if (error) throw new Error("デバイス一覧の取得に失敗しました");
  return data ?? [];
}

async function syncDevices(): Promise<DeviceRead[]> {
  const { data, error, response } = await api.POST("/api/devices/sync");
  if (response.status === 502) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      (body as { detail?: string }).detail ?? "netd との同期に失敗しました（502）"
    );
  }
  if (error || !data) throw new Error("デバイス同期に失敗しました");
  return data;
}

async function fetchExtensions(): Promise<ExtensionRead[]> {
  const { data, error } = await api.GET("/api/extensions");
  if (error) throw new Error("内線一覧の取得に失敗しました");
  return data ?? [];
}

async function quickProvision(
  deviceId: number,
  body: QuickProvisionPayload
): Promise<DeviceRead> {
  const { data, error, response } = await api.POST("/api/devices/{device_id}/quick-provision", {
    params: { path: { device_id: deviceId } },
    body,
  });
  if (response.status === 409) {
    // 別の端末に割当済み（detail に相手端末の MAC・モデル・IP が入る）
    const detail = (error as { detail?: unknown } | undefined)?.detail;
    throw new Error(
      typeof detail === "string" ? detail : "この内線は別の端末に割り当て済みです"
    );
  }
  if (response.status === 404) {
    throw new Error("端末または内線が見つかりません。一覧を更新してください");
  }
  if (response.status === 502) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      (body as { detail?: string }).detail ?? "プロビジョニング中に netd エラーが発生しました（502）"
    );
  }
  if (error || !data) throw new Error("内線割当に失敗しました");
  return data;
}

async function deleteDevice(deviceId: number): Promise<void> {
  const { error } = await api.DELETE("/api/devices/{device_id}", {
    params: { path: { device_id: deviceId } },
  });
  if (error) throw new Error("デバイスの削除に失敗しました");
}

async function fetchRegistrations(): Promise<DeviceRegistrations> {
  const { data, error } = await api.GET("/api/devices/registrations");
  // 取得できない場合も「未確認」として扱う（登録済みと偽らない）
  if (error || !data) return { available: false, registered_device_ids: [] };
  return data;
}

async function resyncDevice(deviceId: number): Promise<ResyncResultRead> {
  const { data, error } = await api.POST("/api/devices/{device_id}/resync", {
    params: { path: { device_id: deviceId } },
  });
  if (error || !data) throw new Error("再取得の指示に失敗しました");
  return data;
}

async function unassignExtension(deviceId: number): Promise<DeviceRead> {
  const { data, error } = await api.PATCH("/api/devices/{device_id}", {
    params: { path: { device_id: deviceId } },
    body: { extension_id: null, reassign: false },
  });
  if (error || !data) throw new Error("内線の割当解除に失敗しました");
  return data;
}

async function updateProvisionMode(deviceId: number, mode: ProvisionMode): Promise<DeviceRead> {
  const { data, error } = await api.PATCH("/api/devices/{device_id}", {
    params: { path: { device_id: deviceId } },
    body: { provision_mode: mode, reassign: false },
  });
  if (error || !data) throw new Error("配信モードの変更に失敗しました");
  return data;
}

async function issueToken(deviceId: number): Promise<ProvisionTokenIssued> {
  const { data, error, response } = await api.POST("/api/devices/{device_id}/token", {
    params: { path: { device_id: deviceId } },
  });
  if (response.status === 409) {
    const body = await response.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "トークンを発行できません");
  }
  if (error || !data) throw new Error("トークンの発行に失敗しました");
  return data;
}

async function revokeToken(deviceId: number): Promise<DeviceRead> {
  const { data, error } = await api.DELETE("/api/devices/{device_id}/token", {
    params: { path: { device_id: deviceId } },
  });
  if (error || !data) throw new Error("トークンの破棄に失敗しました");
  return data;
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// ページコンポーネント
// ---------------------------------------------------------------------------

export function DevicesPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const listQuery = useQuery({ queryKey: DEVICES_KEY, queryFn: fetchDevices });
  const registrationsQuery = useQuery({
    queryKey: DEVICE_REGISTRATIONS_KEY,
    queryFn: fetchRegistrations,
  });

  // 配信モード切替の確認
  const [modeTarget, setModeTarget] = useState<{ device: DeviceRead; mode: ProvisionMode } | null>(
    null
  );
  // トークン破棄の確認
  const [revokeTarget, setRevokeTarget] = useState<DeviceRead | null>(null);
  // 発行したトークン付き URL（一度だけ表示）
  const [issued, setIssued] = useState<{ device: DeviceRead; result: ProvisionTokenIssued } | null>(
    null
  );

  // 内線割当パネル（issue #149: 既定は既存の内線から選ぶ）
  const extensionsQuery = useQuery({ queryKey: EXTENSIONS_KEY, queryFn: fetchExtensions });
  const [provisionTarget, setProvisionTarget] = useState<DeviceRead | null>(null);
  const [provisionForm, setProvisionForm] = useState<AssignForm>(emptyAssignForm());
  const [provisionErrors, setProvisionErrors] = useState<AssignErrors>({});
  // 別の端末に割当済みの内線を選んだときの付け替え確認
  const [reassignHolder, setReassignHolder] = useState<DeviceRead | null>(null);
  // 割当解除の確認
  const [unassignTarget, setUnassignTarget] = useState<DeviceRead | null>(null);

  // 削除確認
  const [deleteTarget, setDeleteTarget] = useState<DeviceRead | null>(null);

  const selection = useObjectSelection(listQuery.data, openProvision, closeProvision);

  // ─── リース同期ミューテーション ───
  const syncMutation = useMutation({
    mutationFn: syncDevices,
    onSuccess: (data) => {
      queryClient.setQueryData(DEVICES_KEY, data);
      toast.success(`リース同期完了（${data.length} 台）`);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "同期に失敗しました");
    },
  });

  // ─── 内線割当ミューテーション ───
  const provisionMutation = useMutation({
    mutationFn: (reassign: boolean) =>
      quickProvision(provisionTarget!.id, buildAssignPayload(provisionForm, reassign)),
    onSuccess: (_device, reassign) => {
      queryClient.invalidateQueries({ queryKey: DEVICES_KEY });
      queryClient.invalidateQueries({ queryKey: EXTENSIONS_KEY });
      toast.success(reassign ? "内線を付け替えました" : "内線を割り当てました");
      setReassignHolder(null);
      setProvisionTarget(null); selection.clear();
    },
    onError: (err) => {
      setReassignHolder(null);
      toast.error(err instanceof Error ? err.message : "内線割当に失敗しました");
    },
  });

  // ─── 割当解除ミューテーション ───
  const unassignMutation = useMutation({
    mutationFn: (id: number) => unassignExtension(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: DEVICES_KEY });
      queryClient.invalidateQueries({ queryKey: EXTENSIONS_KEY });
      toast.success("内線の割当を解除しました");
      setUnassignTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "内線の割当解除に失敗しました");
    },
  });

  // ─── 削除ミューテーション ───
  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteDevice(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: DEVICES_KEY });
      toast.success("デバイスを削除しました");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "削除に失敗しました");
    },
  });

  // ─── 再同期（再取得の指示）ミューテーション ───
  const resyncMutation = useMutation({
    mutationFn: (id: number) => resyncDevice(id),
    onSuccess: (result) => {
      const t = resyncToast(result);
      if (t.tone === "success") toast.success(t.message);
      else toast.error(t.message);
      queryClient.invalidateQueries({ queryKey: DEVICES_KEY });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "再取得の指示に失敗しました");
    },
  });

  // ─── 配信モード切替ミューテーション ───
  const modeMutation = useMutation({
    mutationFn: ({ id, mode }: { id: number; mode: ProvisionMode }) => updateProvisionMode(id, mode),
    onSuccess: (device) => {
      queryClient.invalidateQueries({ queryKey: DEVICES_KEY });
      toast.success(
        device.provision_mode === "strict"
          ? "厳格モードにしました。トークンを発行して電話機に入力してください"
          : "標準モードに戻しました（トークンは破棄しました）"
      );
      setModeTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "配信モードの変更に失敗しました");
    },
  });

  // ─── トークン発行ミューテーション ───
  const issueMutation = useMutation({
    mutationFn: (device: DeviceRead) => issueToken(device.id),
    onSuccess: (result, device) => {
      queryClient.invalidateQueries({ queryKey: DEVICES_KEY });
      setIssued({ device, result });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "トークンの発行に失敗しました");
    },
  });

  // ─── トークン破棄ミューテーション ───
  const revokeMutation = useMutation({
    mutationFn: (id: number) => revokeToken(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: DEVICES_KEY });
      toast.success("トークンを破棄しました");
      setRevokeTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "トークンの破棄に失敗しました");
    },
  });

  // ─── 内線割当パネル制御 ───
  function openProvision(device: DeviceRead) {
    selection.select(device.id);
    setProvisionTarget(device);
    setProvisionForm(emptyAssignForm());
    setProvisionErrors({});
    setReassignHolder(null);
  }

  function setAssignMode(mode: AssignMode) {
    setProvisionForm((f) => ({ ...f, mode }));
    setProvisionErrors({});
  }

  function closeProvision() {
    setProvisionTarget(null); selection.clear();
  }

  const assignOptions = provisionTarget
    ? extensionOptions(extensionsQuery.data ?? [], listQuery.data ?? [], provisionTarget.id)
    : [];

  function handleProvisionSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errors = validateAssignForm(provisionForm);
    setProvisionErrors(errors);
    if (Object.keys(errors).length > 0) return;
    if (provisionForm.mode === "existing") {
      const option = assignOptions.find((o) => String(o.id) === provisionForm.extension_id);
      if (option?.holder) {
        // 別の端末に割当済み → 付け替えの確認を挟む
        setReassignHolder(option.holder);
        return;
      }
    }
    provisionMutation.mutate(false);
  }

  // ─── テーブルカラム定義 ───
  const columns: Column<DeviceRead>[] = [
    {
      key: "mac_address",
      header: "MAC アドレス",
      width: "160px",
      render: (row) => (
        <span className={css({ fontFamily: "monospace", fontSize: "sm" })}>
          {row.mac_address}
        </span>
      ),
    },
    {
      key: "ip_address",
      header: "IP アドレス",
      width: "140px",
      render: (row) =>
        row.ip_address ? (
          <span className={css({ fontFamily: "monospace", fontSize: "sm" })}>
            {row.ip_address}
          </span>
        ) : (
          <span className={css({ color: "text.subtle", fontSize: "sm" })}>—</span>
        ),
    },
    {
      key: "hostname",
      header: "ホスト名",
      render: (row) =>
        row.hostname ? (
          <span>{row.hostname}</span>
        ) : (
          <span className={css({ color: "text.subtle", fontSize: "sm" })}>—</span>
        ),
    },
    {
      key: "model",
      header: "モデル",
      width: "140px",
      render: (row) =>
        row.model ? (
          <span>{row.model}</span>
        ) : (
          <span className={css({ color: "text.subtle", fontSize: "sm" })}>—</span>
        ),
    },
    {
      key: "extension",
      header: "内線",
      width: "180px",
      render: (row) =>
        row.extension_number ? (
          <div>
            {row.extension_id != null ? (
              <Link to="/extensions" search={{ selected: String(row.extension_id) } as never}
                aria-label={`内線 ${row.extension_number} の詳細を開く`}
                className={css({ color: "accent.text", textDecoration: "underline", fontWeight: "600", fontSize: "sm" })}>
                {row.extension_number}
              </Link>
            ) : <span className={css({ fontWeight: "600", fontSize: "sm" })}>{row.extension_number}</span>}
            {row.extension_display_name ? (
              <span className={css({ color: "text.muted", fontSize: "sm", ml: "1" })}>
                {row.extension_display_name}
              </span>
            ) : null}
          </div>
        ) : (
          <span className={css({ color: "text.subtle", fontSize: "sm" })}>未割当</span>
        ),
    },
    {
      key: "status",
      header: "状態",
      width: "200px",
      render: (row) => {
        const s = deviceStatus(row, registrationsQuery.data ?? null);
        return (
          <div title={s.hint}>
            <div className={css({ display: "flex", alignItems: "center", gap: "1" })}>
              {s.kind === "complete" ? (
                <CheckCircle2 size={14} className={css({ color: "green.600" })} />
              ) : (
                <Circle size={14} className={css({ color: "text.subtle" })} />
              )}
              <span className={badge({ tone: s.tone })}>{s.label}</span>
            </div>
            {row.last_config_fetch_at ? (
              <p className={css({ fontSize: "xs", color: "text.muted", mt: "1" })}>
                取得 {formatDateTime(row.last_config_fetch_at)}
                {row.last_config_fetch_used_token ? "（トークン）" : ""}
              </p>
            ) : null}
          </div>
        );
      },
    },
    {
      key: "provision_mode",
      header: "配信",
      width: "110px",
      render: (row) =>
        row.provision_mode === "strict" ? (
          <span
            className={badge({ tone: "accent" })}
            title="端末固有の設定はトークン付き URL からのみ配信します"
          >
            厳格
          </span>
        ) : (
          <span
            className={badge({ tone: "neutral" })}
            title="LAN 内・登録済み端末・IP 一致で配信します"
          >
            標準
          </span>
        ),
    },
    {
      key: "last_seen",
      header: "最終確認",
      width: "160px",
      render: (row) =>
        row.last_seen ? (
          <span className={css({ fontSize: "sm", color: "text.muted" })}>
            {formatDateTime(row.last_seen)}
          </span>
        ) : (
          <span className={css({ color: "text.subtle", fontSize: "sm" })}>—</span>
        ),
    },
    {
      key: "actions",
      header: "操作",
      width: "420px",
      align: "right",
      render: (row) => (
        <div
          className={css({
            display: "flex",
            gap: "2",
            justifyContent: "flex-end",
            flexWrap: "wrap",
          })}
        >
          {row.extension_id == null ? (
            <button
              type="button"
              className={button({ variant: "secondary", size: "sm" })}
              style={{ height: "28px" }}
              onClick={() => openProvision(row)}
            >
              <PhoneCall size={13} />
              内線割当
            </button>
          ) : (
            <>
              <button
                type="button"
                className={button({ variant: "ghost", size: "sm" })}
                style={{ height: "28px" }}
                onClick={() => openProvision(row)}
              >
                <PhoneCall size={13} />
                内線を変更
              </button>
              <button
                type="button"
                className={button({ variant: "ghost", size: "sm" })}
                style={{ height: "28px" }}
                onClick={() => setUnassignTarget(row)}
              >
                <Unlink size={13} />
                割当を解除
              </button>
            </>
          )}
          {row.provisioned && (
            <button
              type="button"
              className={button({ variant: "secondary", size: "sm" })}
              style={{ height: "28px" }}
              title="電話機に設定の再取得を指示します（取得したかは状態列で確認）"
              disabled={resyncMutation.isPending && resyncMutation.variables === row.id}
              onClick={() => resyncMutation.mutate(row.id)}
            >
              {resyncMutation.isPending && resyncMutation.variables === row.id ? (
                <Loader2 size={13} className={css({ animation: "spin" })} />
              ) : (
                <RotateCw size={13} />
              )}
              再同期
            </button>
          )}
          {row.provision_mode === "strict" && (
            <button
              type="button"
              className={button({ variant: "secondary", size: "sm" })}
              style={{ height: "28px" }}
              disabled={issueMutation.isPending}
              onClick={() => issueMutation.mutate(row)}
            >
              <KeyRound size={13} />
              {row.token_issued ? "トークン再発行" : "トークン発行"}
            </button>
          )}
          {row.provision_mode === "strict" && row.token_issued && (
            <button
              type="button"
              className={button({ variant: "ghost", size: "sm" })}
              style={{ height: "28px" }}
              onClick={() => setRevokeTarget(row)}
            >
              トークン破棄
            </button>
          )}
          <button
            type="button"
            className={button({ variant: "ghost", size: "sm" })}
            style={{ height: "28px" }}
            onClick={() =>
              setModeTarget({
                device: row,
                mode: row.provision_mode === "strict" ? "standard" : "strict",
              })
            }
          >
            {row.provision_mode === "strict" ? <ShieldOff size={13} /> : <ShieldCheck size={13} />}
            {row.provision_mode === "strict" ? "標準に戻す" : "厳格モード"}
          </button>
          <button
            type="button"
            className={button({ variant: "ghost", size: "sm" })}
            style={{ height: "28px" }}
            onClick={() => setDeleteTarget(row)}
          >
            <Trash2 size={13} />
            削除
          </button>
        </div>
      ),
    },
  ];

  return (
    <PageLayout
      title="デバイス"
      description="DHCP リースから検出された SIP 端末の管理・内線割当"
      actions={
        <button
          type="button"
          className={button({ variant: "secondary" })}
          style={{ height: "36px" }}
          onClick={() => syncMutation.mutate()}
          disabled={syncMutation.isPending}
        >
          {syncMutation.isPending ? (
            <Loader2 size={16} className={css({ animation: "spin" })} />
          ) : (
            <RefreshCw size={16} />
          )}
          {syncMutation.isPending ? "同期中…" : "リース同期"}
        </button>
      }
    >
      {/* デバイス一覧 */}
      <DataTable
        columns={columns}
        rows={listQuery.data ?? []}
        rowKey={(row) => row.id}
        loading={listQuery.isLoading}
        error={listQuery.error}
        onRetry={() => void listQuery.refetch()}
        searchable
        emptyMessage="デバイスが見つかりません。「リース同期」で DHCP リースを取り込んでください。"
      />

      {/* 内線割当スライドパネル */}
      <SlidePanel
        busy={provisionMutation.isPending}
        open={provisionTarget !== null}
        title={provisionTarget?.extension_id != null ? "内線を変更する" : "内線を割り当てる"}
        onClose={closeProvision}
        footer={
          <>
            <button
              type="button"
              className={button({ variant: "secondary" })}
              onClick={closeProvision}
              disabled={provisionMutation.isPending}
            >
              キャンセル
            </button>
            <button
              type="submit"
              form="quick-provision-form"
              className={button({ variant: "primary" })}
              style={{ height: "36px" }}
              disabled={provisionMutation.isPending}
            >
              {provisionMutation.isPending ? "割当中…" : "割り当てる"}
            </button>
          </>
        }
      >
        {provisionTarget && (
          <form
            id="quick-provision-form"
            onSubmit={handleProvisionSubmit}
            className={css({ display: "flex", flexDirection: "column", gap: "4" })}
          >
            {/* 対象デバイス情報表示 */}
            <div
              className={css({
                p: "3",
                borderRadius: "md",
                bg: "gray.50",
                borderWidth: "1px",
                borderStyle: "solid",
                borderColor: "border",
                display: "flex",
                alignItems: "center",
                gap: "3",
              })}
            >
              <Smartphone size={18} className={css({ color: "text.muted", flexShrink: 0 })} />
              <div>
                <p className={css({ fontFamily: "monospace", fontSize: "sm", fontWeight: "600" })}>
                  {provisionTarget.mac_address}
                </p>
                {provisionTarget.ip_address && (
                  <p className={css({ fontSize: "sm", color: "text.muted", fontFamily: "monospace" })}>
                    {provisionTarget.ip_address}
                  </p>
                )}
                {provisionTarget.hostname && (
                  <p className={css({ fontSize: "sm", color: "text.muted" })}>
                    {provisionTarget.hostname}
                  </p>
                )}
              </div>
            </div>

            {provisionTarget.extension_number && (
              <p className={css({ fontSize: "sm", color: "text.muted" })}>
                現在の内線: {provisionTarget.extension_number}
                {provisionTarget.extension_display_name
                  ? ` ${provisionTarget.extension_display_name}`
                  : ""}
              </p>
            )}

            <div
              role="radiogroup"
              aria-label="割当方法"
              className={css({ display: "flex", flexDirection: "column", gap: "2" })}
            >
              {(
                [
                  ["existing", "既存の内線から選ぶ"],
                  ["new", "新しい内線を作成して割り当てる"],
                ] as const
              ).map(([mode, label]) => (
                <label
                  key={mode}
                  className={css({
                    display: "flex",
                    alignItems: "center",
                    gap: "2",
                    fontSize: "sm",
                    cursor: "pointer",
                  })}
                >
                  <input
                    type="radio"
                    name="assign-mode"
                    value={mode}
                    checked={provisionForm.mode === mode}
                    onChange={() => setAssignMode(mode)}
                  />
                  {label}
                </label>
              ))}
            </div>

            {provisionForm.mode === "existing" ? (
              <ProvisionField required label="内線" error={provisionErrors.extension_id}>
                <select
                  className={input({ invalid: provisionErrors.extension_id ? true : undefined })}
                  value={provisionForm.extension_id}
                  onChange={(e) =>
                    setProvisionForm((f) => ({ ...f, extension_id: e.target.value }))
                  }
                  disabled={extensionsQuery.isLoading}
                >
                  <option value="">
                    {extensionsQuery.isLoading ? "読み込み中…" : "内線を選んでください"}
                  </option>
                  {assignOptions.map((o) => (
                    <option key={o.id} value={String(o.id)}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </ProvisionField>
            ) : (
              <>
                <ProvisionField required label="内線番号" error={provisionErrors.extension_number}>
                  <input
                    className={input({ invalid: provisionErrors.extension_number ? true : undefined })}
                    value={provisionForm.extension_number}
                    onChange={(e) =>
                      setProvisionForm((f) => ({ ...f, extension_number: e.target.value }))
                    }
                    inputMode="numeric"
                    placeholder="1001"
                  />
                </ProvisionField>

                <ProvisionField required label="表示名" error={provisionErrors.display_name}>
                  <input
                    className={input({ invalid: provisionErrors.display_name ? true : undefined })}
                    value={provisionForm.display_name}
                    onChange={(e) =>
                      setProvisionForm((f) => ({ ...f, display_name: e.target.value }))
                    }
                    placeholder="営業部 田中"
                  />
                </ProvisionField>
              </>
            )}
            {provisionForm.mode === "existing" &&
            !extensionsQuery.isLoading &&
            assignOptions.length === 0 ? (
              <p className={css({ fontSize: "sm", color: "text.muted" })}>
                内線がまだありません。
                <Link
                  to="/extensions"
                  className={css({ color: "accent.text", textDecoration: "underline" })}
                >
                  内線
                </Link>
                の画面で作成するか、「新しい内線を作成して割り当てる」を選んでください。
              </p>
            ) : null}
          </form>
        )}
      </SlidePanel>

      {/* 付け替えの確認（別の端末に割当済みの内線を選んだとき） */}
      <ConfirmDialog
        open={reassignHolder !== null}
        title="内線を付け替える"
        message={
          reassignHolder && provisionTarget
            ? `この内線は端末 ${deviceShortLabel(reassignHolder)}${reassignHolder.ip_address ? `・${reassignHolder.ip_address}` : ""} に割り当て済みです。付け替えると、その端末の割当は解除され設定を取得できなくなります。${provisionTarget.mac_address} に付け替えますか？`
            : ""
        }
        confirmLabel="付け替える"
        busy={provisionMutation.isPending}
        onConfirm={() => provisionMutation.mutate(true)}
        onCancel={() => setReassignHolder(null)}
      />

      {/* 割当解除の確認 */}
      <ConfirmDialog
        open={unassignTarget !== null}
        title="内線の割当を解除"
        message={
          unassignTarget
            ? `${unassignTarget.mac_address} から内線 ${unassignTarget.extension_number ?? ""} の割当を解除します。端末は未割当に戻り、内線を割り当て直すまで設定を取得できなくなります（内線そのものは削除しません）。`
            : ""
        }
        confirmLabel="解除"
        destructive
        busy={unassignMutation.isPending}
        onConfirm={() => unassignTarget && unassignMutation.mutate(unassignTarget.id)}
        onCancel={() => setUnassignTarget(null)}
      />

      {/* 削除確認ダイアログ */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="デバイスを削除"
        message={
          deleteTarget
            ? `デバイス ${deleteTarget.mac_address}${deleteTarget.ip_address ? `（${deleteTarget.ip_address}）` : ""} を削除します。この操作は取り消せません。`
            : ""
        }
        confirmLabel="削除"
        destructive
        busy={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />

      {/* 配信モード切替の確認 */}
      <ConfirmDialog
        open={modeTarget !== null}
        title={modeTarget?.mode === "strict" ? "厳格モードにする" : "標準モードに戻す"}
        message={
          modeTarget
            ? modeTarget.mode === "strict"
              ? `${modeTarget.device.mac_address} の設定ファイルを、トークン付き URL からのみ配信します。トークンを発行して電話機の Web 画面に入力するまで、この端末は設定を取得できません（通常の URL は 404 になります）。`
              : `${modeTarget.device.mac_address} のトークンを破棄し、LAN 内・登録済み端末・IP 一致での配信に戻します。電話機に入力したトークン付き URL は使えなくなるため、電話機側のプロビジョニング URL も通常の URL に戻してください。`
            : ""
        }
        confirmLabel={modeTarget?.mode === "strict" ? "厳格モードにする" : "標準に戻す"}
        busy={modeMutation.isPending}
        onConfirm={() =>
          modeTarget && modeMutation.mutate({ id: modeTarget.device.id, mode: modeTarget.mode })
        }
        onCancel={() => setModeTarget(null)}
      />

      {/* トークン破棄の確認 */}
      <ConfirmDialog
        open={revokeTarget !== null}
        title="トークンを破棄"
        message={
          revokeTarget
            ? `${revokeTarget.mac_address} のトークンを破棄します。厳格モードのままなので、新しいトークンを発行して電話機に入力するまで、この端末は設定を取得できなくなります。`
            : ""
        }
        confirmLabel="破棄"
        destructive
        busy={revokeMutation.isPending}
        onConfirm={() => revokeTarget && revokeMutation.mutate(revokeTarget.id)}
        onCancel={() => setRevokeTarget(null)}
      />

      {/* 発行したトークン付き URL（一度だけ表示） */}
      {issued && (
        <TokenIssuedDialog
          device={issued.device}
          result={issued.result}
          onClose={() => setIssued(null)}
        />
      )}
    </PageLayout>
  );
}

// ---------------------------------------------------------------------------
// 補助コンポーネント
// ---------------------------------------------------------------------------

/**
 * 発行したトークン付き URL を一度だけ表示するダイアログ。
 * 閉じると二度と表示できない（サーバーはハッシュのみ保存）。管理者が電話機の Web 画面へ手入力する。
 */
function TokenIssuedDialog({
  device,
  result,
  onClose,
}: {
  device: DeviceRead;
  result: ProvisionTokenIssued;
  onClose: () => void;
}) {
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
        className={css({ position: "absolute", inset: 0, bg: "gray.900", opacity: 0.2 })}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="トークン付き URL"
        className={cx(
          panel(),
          css({
            position: "relative",
            w: "640px",
            maxW: "100%",
            p: "5",
            display: "flex",
            flexDirection: "column",
            gap: "4",
          })
        )}
      >
        <div className={css({ display: "flex", alignItems: "center", gap: "2" })}>
          <AlertTriangle size={18} className={css({ color: "warn.text" })} />
          <h2 className={css({ fontSize: "lg", fontWeight: "600" })}>
            トークン付き URL（{device.mac_address}）
          </h2>
        </div>
        <p className={css({ fontSize: "sm", color: "text.muted" })}>
          この URL は<strong>この画面でしか表示されません</strong>。電話機の Web
          管理画面に入力してください。閉じた後に必要になった場合は再発行してください
          （再発行すると古い URL は使えなくなります）。電話機側で DHCP
          のプロビジョニング（option 66）が有効だと通常の URL で上書きされることがあるため、
          無効にしてください。
        </p>
        <CopyRow
          label="Yealink: 自動プロビジョニングのサーバー URL"
          value={result.yealink_server_url}
        />
        <CopyRow
          label="Panasonic: Standard File URL"
          value={result.panasonic_standard_file_url}
        />
        <CopyRow label="Panasonic: Master File URL" value={result.panasonic_master_file_url} />
        <p className={css({ fontSize: "sm", color: "text.muted" })}>
          入力後、電話機を再起動するか「再同期」を押し、状態列が「完了」になることを確認してください。
        </p>
        <div className={css({ display: "flex", justifyContent: "flex-end" })}>
          <button
            type="button"
            className={button({ variant: "primary" })}
            style={{ height: "36px" }}
            onClick={onClose}
          >
            入力しました
          </button>
        </div>
      </div>
    </div>
  );
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("コピーに失敗しました");
    }
  };
  return (
    <div>
      <span className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}>
        {label}
      </span>
      <div className={css({ display: "flex", gap: "2", alignItems: "center" })}>
        <code
          className={css({
            flex: "1",
            fontFamily: "mono",
            fontSize: "sm",
            p: "2",
            bg: "gray.50",
            borderRadius: "sm",
            borderWidth: "1px",
            borderStyle: "solid",
            borderColor: "border",
            wordBreak: "break-all",
          })}
        >
          {value}
        </code>
        <button
          type="button"
          className={button({ variant: "secondary", size: "sm" })}
          style={{ height: "32px" }}
          onClick={copy}
          aria-label={`${label} をコピー`}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
          {copied ? "コピー済み" : "コピー"}
        </button>
      </div>
    </div>
  );
}

/** フォーム 1 項目（ラベル + 入力 + インラインエラー）。ExtensionsPage と同パターン。 */
