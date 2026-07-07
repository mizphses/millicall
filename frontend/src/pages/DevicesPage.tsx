/**
 * デバイス管理ページ（Phase 5 Task 6）。
 *
 * セクション:
 *   (a) デバイス一覧テーブル（MAC / IP / ホスト名 / モデル / 内線 / provisioned バッジ / 最終確認）
 *   (b) 「リース同期」ボタン → POST /api/devices/sync（スピナー付き・502 エラー表示）
 *   (c) 未プロビジョニングデバイスへの「内線割当」アクション → SlidePanel でフォーム入力
 *       → POST /api/devices/{id}/quick-provision
 *   (d) 削除アクション（confirm ダイアログ）→ DELETE /api/devices/{id}
 *
 * デザイン原則:
 *   - PandaCSS css()/cx() + styled-system/recipes (panel, button, input, badge)
 *   - lucide-react アイコン（サイズは明示的な px 値）
 *   - ExtensionsPage / NetworkPage のハウススタイルに準拠
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  RefreshCw,
  Smartphone,
  Trash2,
  PhoneCall,
  Loader2,
  CheckCircle2,
  Circle,
} from "lucide-react";

import { css } from "styled-system/css";
import { badge, button, input } from "styled-system/recipes";

import { api } from "../api/client";
import type { components } from "../api/schema.d";
import { DEVICES_KEY } from "../queryKeys";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useToast } from "../toast/ToastProvider";

// ---------------------------------------------------------------------------
// 型エイリアス（schema.d.ts の自動生成型を再利用）
// ---------------------------------------------------------------------------

type DeviceRead = components["schemas"]["DeviceRead"];

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

async function quickProvision(
  deviceId: number,
  extensionNumber: string,
  displayName: string
): Promise<DeviceRead> {
  const { data, error, response } = await api.POST("/api/devices/{device_id}/quick-provision", {
    params: { path: { device_id: deviceId } },
    body: { extension_number: extensionNumber, display_name: displayName },
  });
  if (response.status === 409) {
    throw new Error("この内線番号は既に使用されています");
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

// ---------------------------------------------------------------------------
// フォーム状態型
// ---------------------------------------------------------------------------

type QuickProvisionForm = {
  extension_number: string;
  display_name: string;
};

function emptyProvisionForm(): QuickProvisionForm {
  return { extension_number: "", display_name: "" };
}

// ---------------------------------------------------------------------------
// ページコンポーネント
// ---------------------------------------------------------------------------

export function DevicesPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const listQuery = useQuery({ queryKey: DEVICES_KEY, queryFn: fetchDevices });

  // 内線割当パネル
  const [provisionTarget, setProvisionTarget] = useState<DeviceRead | null>(null);
  const [provisionForm, setProvisionForm] = useState<QuickProvisionForm>(emptyProvisionForm());
  const [provisionErrors, setProvisionErrors] = useState<
    Partial<Record<"extension_number" | "display_name", string>>
  >({});

  // 削除確認
  const [deleteTarget, setDeleteTarget] = useState<DeviceRead | null>(null);

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
    mutationFn: () =>
      quickProvision(
        provisionTarget!.id,
        provisionForm.extension_number.trim(),
        provisionForm.display_name.trim()
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: DEVICES_KEY });
      toast.success("内線を割り当てました");
      setProvisionTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "内線割当に失敗しました");
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

  // ─── 内線割当パネル制御 ───
  function openProvision(device: DeviceRead) {
    setProvisionTarget(device);
    setProvisionForm(emptyProvisionForm());
    setProvisionErrors({});
  }

  function closeProvision() {
    setProvisionTarget(null);
  }

  function handleProvisionSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errors: Partial<Record<"extension_number" | "display_name", string>> = {};
    if (!provisionForm.extension_number.trim()) {
      errors.extension_number = "内線番号を入力してください";
    }
    if (!provisionForm.display_name.trim()) {
      errors.display_name = "表示名を入力してください";
    }
    setProvisionErrors(errors);
    if (Object.keys(errors).length > 0) return;
    provisionMutation.mutate();
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
            <span className={css({ fontWeight: "600", fontSize: "sm" })}>
              {row.extension_number}
            </span>
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
      key: "provisioned",
      header: "プロビジョニング",
      width: "140px",
      render: (row) =>
        row.provisioned ? (
          <div className={css({ display: "flex", alignItems: "center", gap: "1" })}>
            <CheckCircle2 size={14} className={css({ color: "green.600" })} />
            <span className={badge({ tone: "success" })}>完了</span>
          </div>
        ) : (
          <div className={css({ display: "flex", alignItems: "center", gap: "1" })}>
            <Circle size={14} className={css({ color: "text.subtle" })} />
            <span className={badge({ tone: "neutral" })}>未完了</span>
          </div>
        ),
    },
    {
      key: "last_seen",
      header: "最終確認",
      width: "160px",
      render: (row) =>
        row.last_seen ? (
          <span className={css({ fontSize: "sm", color: "text.muted" })}>
            {new Date(row.last_seen).toLocaleString("ja-JP", {
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        ) : (
          <span className={css({ color: "text.subtle", fontSize: "sm" })}>—</span>
        ),
    },
    {
      key: "actions",
      header: "操作",
      width: "200px",
      align: "right",
      render: (row) => (
        <div className={css({ display: "flex", gap: "2", justifyContent: "flex-end" })}>
          {!row.provisioned && (
            <button
              type="button"
              className={button({ variant: "secondary", size: "sm" })}
              style={{ height: "28px" }}
              onClick={() => openProvision(row)}
            >
              <PhoneCall size={13} />
              内線割当
            </button>
          )}
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
        emptyMessage="デバイスが見つかりません。「リース同期」で DHCP リースを取り込んでください。"
      />

      {/* 内線割当スライドパネル */}
      <SlidePanel
        open={provisionTarget !== null}
        title="内線を割り当てる"
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

            <ProvisionField label="内線番号" error={provisionErrors.extension_number}>
              <input
                className={input({ invalid: provisionErrors.extension_number ? true : undefined })}
                value={provisionForm.extension_number}
                onChange={(e) =>
                  setProvisionForm((f) => ({ ...f, extension_number: e.target.value }))
                }
                inputMode="numeric"
                placeholder="1001"
                autoFocus
              />
            </ProvisionField>

            <ProvisionField label="表示名" error={provisionErrors.display_name}>
              <input
                className={input({ invalid: provisionErrors.display_name ? true : undefined })}
                value={provisionForm.display_name}
                onChange={(e) =>
                  setProvisionForm((f) => ({ ...f, display_name: e.target.value }))
                }
                placeholder="営業部 田中"
              />
            </ProvisionField>
          </form>
        )}
      </SlidePanel>

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
    </PageLayout>
  );
}

// ---------------------------------------------------------------------------
// 補助コンポーネント
// ---------------------------------------------------------------------------

/** フォーム 1 項目（ラベル + 入力 + インラインエラー）。ExtensionsPage と同パターン。 */
function ProvisionField({
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
