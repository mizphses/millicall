/**
 * ネットワーク（外向き）設定ページ。
 *
 * リモートアクセス側の機能を扱う:
 *   - Tailscale VPN（有効化トグル + 認証キー入力 + 接続/切断 + ステータス）
 *
 * 認証キーは書き込み専用。tailscale_auth_key_set=true のときは「設定済み」と表示し、
 * 平文は絶対に表示しない。
 *
 * LAN 側（LAN / DHCP / NAT）は「ネットワーク（内向き）」
 * （NetworkPage / /network）に分離している。
 *
 * デザイン原則:
 *   - PandaCSS css()/cx() + styled-system/recipes (panel, button, input)
 *   - lucide-react アイコン（サイズは明示的な px 値）
 *   - 既存の ProvidersPage / RoutesPage のハウススタイルに準拠
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import {
  Shield,
  CheckCircle2,
  XCircle,
  Loader2,
  RefreshCw,
  Play,
  Square,
  KeyRound,
} from "lucide-react";

import { css, cx } from "styled-system/css";
import { button, input } from "styled-system/recipes";

import { api } from "../api/client";
import {
  NETWORK_CONFIG_KEY,
  NETWORK_TAILSCALE_STATUS_KEY,
} from "../queryKeys";
import { PageLayout } from "../components/PageLayout";
import { useToast } from "../toast/ToastProvider";
import {
  Field,
  SectionCard,
  fetchNetworkConfig,
  updateNetworkConfig,
  type NetworkConfigUpdate,
} from "./network/shared";

// ---------------------------------------------------------------------------
// API 関数
// ---------------------------------------------------------------------------

async function fetchTailscaleStatus(): Promise<{ connected: boolean; error?: string | null }> {
  const { data, error } = await api.GET("/api/network/tailscale/status");
  if (error || !data) return { connected: false, error: "ステータスの取得に失敗しました" };
  return data as { connected: boolean; error?: string | null };
}

async function tailscaleUp(): Promise<void> {
  const { error, response } = await api.POST("/api/network/tailscale/up");
  if (response.status === 400) {
    const body = await response.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "auth key が未設定です");
  }
  if (response.status === 502) {
    const body = await response.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "tailscale up に失敗しました（502）");
  }
  if (error) throw new Error("tailscale up に失敗しました");
}

async function tailscaleDown(): Promise<void> {
  const { error, response } = await api.POST("/api/network/tailscale/down");
  if (response.status === 502) {
    const body = await response.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "tailscale down に失敗しました（502）");
  }
  if (error) throw new Error("tailscale down に失敗しました");
}

// ---------------------------------------------------------------------------
// フォーム状態型
// ---------------------------------------------------------------------------

type TailscaleForm = {
  tailscale_enabled: boolean;
  tailscale_auth_key: string;
};

// ---------------------------------------------------------------------------
// ページコンポーネント
// ---------------------------------------------------------------------------

const PAGE_TITLE = "ネットワーク（外向き）";
const PAGE_DESCRIPTION = "リモートアクセス側の設定（Tailscale VPN）";

export function NetworkRemotePage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const configQuery = useQuery({
    queryKey: NETWORK_CONFIG_KEY,
    queryFn: fetchNetworkConfig,
  });

  const statusQuery = useQuery({
    queryKey: NETWORK_TAILSCALE_STATUS_KEY,
    queryFn: fetchTailscaleStatus,
    refetchInterval: 15_000, // 15 秒ごとに自動更新
  });

  const cfg = configQuery.data;

  const [tsForm, setTsForm] = useState<TailscaleForm>({
    tailscale_enabled: false,
    tailscale_auth_key: "",
  });

  // サーバーデータがロード済みになったらフォームを初期化する
  useEffect(() => {
    if (cfg) {
      setTsForm({
        tailscale_enabled: cfg.tailscale_enabled,
        tailscale_auth_key: "",
      });
    }
  }, [cfg]);

  // Tailscale 設定保存
  const saveTsMutation = useMutation({
    mutationFn: () => {
      if (!cfg) throw new Error("設定データがロードされていません");
      const payload: NetworkConfigUpdate = {
        lan_interface: cfg.lan_interface,
        lan_ip: cfg.lan_ip,
        lan_prefix: cfg.lan_prefix,
        dhcp_range_start: cfg.dhcp_range_start,
        dhcp_range_end: cfg.dhcp_range_end,
        dhcp_lease_hours: cfg.dhcp_lease_hours,
        provisioning_base_url: cfg.provisioning_base_url,
        nat_enabled: cfg.nat_enabled,
        wan_interface: cfg.wan_interface,
        tailscale_enabled: tsForm.tailscale_enabled,
        // 空文字列はキー削除、非空は更新、未入力（フォームが空）は null（変更しない）
        tailscale_auth_key: tsForm.tailscale_auth_key === "" ? null : tsForm.tailscale_auth_key,
      };
      return updateNetworkConfig(payload);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(NETWORK_CONFIG_KEY, data);
      // 保存後は入力フィールドをクリア（書き込み専用）
      setTsForm((f) => ({ ...f, tailscale_auth_key: "" }));
      toast.success("Tailscale 設定を保存しました");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Tailscale 設定の保存に失敗しました");
    },
  });

  // Tailscale 接続
  const tsUpMutation = useMutation({
    mutationFn: tailscaleUp,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: NETWORK_TAILSCALE_STATUS_KEY });
      toast.success("Tailscale を接続しました");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Tailscale の接続に失敗しました");
    },
  });

  // Tailscale 切断
  const tsDownMutation = useMutation({
    mutationFn: tailscaleDown,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: NETWORK_TAILSCALE_STATUS_KEY });
      toast.success("Tailscale を切断しました");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Tailscale の切断に失敗しました");
    },
  });

  if (configQuery.isLoading) {
    return (
      <PageLayout title={PAGE_TITLE} description={PAGE_DESCRIPTION}>
        <p className={css({ color: "text.muted", py: "6" })}>読み込み中…</p>
      </PageLayout>
    );
  }

  if (configQuery.isError || !cfg) {
    return (
      <PageLayout title={PAGE_TITLE} description={PAGE_DESCRIPTION}>
        <p className={css({ color: "danger.text", py: "6" })}>
          ネットワーク設定の取得に失敗しました。ページを再読み込みしてください。
        </p>
      </PageLayout>
    );
  }

  const tailscaleStatus = statusQuery.data;
  const isConnected = tailscaleStatus?.connected ?? false;

  return (
    <PageLayout title={PAGE_TITLE} description={PAGE_DESCRIPTION}>
      <div className={css({ display: "flex", flexDirection: "column", gap: "6" })}>

        {/* ─── Tailscale セクション ─── */}
        <SectionCard
          icon={<Shield size={18} />}
          title="Tailscale VPN"
          description="Tailscale VPN の接続設定と操作を行います"
        >
          <div className={css({ display: "flex", flexDirection: "column", gap: "5" })}>

            {/* ステータス表示 */}
            <div
              className={css({
                display: "flex",
                alignItems: "center",
                gap: "3",
                p: "3",
                borderRadius: "md",
                bg: isConnected ? "green.50" : "gray.50",
                borderWidth: "1px",
                borderStyle: "solid",
                borderColor: isConnected ? "green.200" : "gray.200",
              })}
            >
              {statusQuery.isFetching ? (
                <Loader2 size={18} className={css({ color: "text.muted", animation: "spin" })} />
              ) : isConnected ? (
                <CheckCircle2 size={18} className={css({ color: "green.600" })} />
              ) : (
                <XCircle size={18} className={css({ color: "gray.500" })} />
              )}
              <div className={css({ flex: "1" })}>
                <span
                  className={css({
                    fontWeight: "600",
                    fontSize: "md",
                    color: isConnected ? "green.700" : "text.muted",
                  })}
                >
                  {isConnected ? "接続中" : "切断中"}
                </span>
                {tailscaleStatus?.error ? (
                  <p className={css({ fontSize: "sm", color: "danger.text", mt: "1" })}>
                    {tailscaleStatus.error}
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                className={button({ variant: "ghost", size: "sm" })}
                onClick={() => statusQuery.refetch()}
                disabled={statusQuery.isFetching}
                title="ステータスを更新"
              >
                <RefreshCw size={14} />
              </button>
            </div>

            {/* 接続/切断 操作 */}
            <div className={css({ display: "flex", gap: "2" })}>
              <button
                type="button"
                className={button({ variant: "primary" })}
                style={{ height: "36px" }}
                onClick={() => tsUpMutation.mutate()}
                disabled={tsUpMutation.isPending || tsDownMutation.isPending || isConnected}
              >
                <Play size={16} />
                {tsUpMutation.isPending ? "接続中…" : "接続"}
              </button>
              <button
                type="button"
                className={button({ variant: "secondary" })}
                style={{ height: "36px" }}
                onClick={() => tsDownMutation.mutate()}
                disabled={tsUpMutation.isPending || tsDownMutation.isPending || !isConnected}
              >
                <Square size={16} />
                {tsDownMutation.isPending ? "切断中…" : "切断"}
              </button>
            </div>

            {/* Tailscale 設定フォーム */}
            <form
              id="tailscale-form"
              onSubmit={(e) => {
                e.preventDefault();
                saveTsMutation.mutate();
              }}
              className={css({ display: "flex", flexDirection: "column", gap: "4" })}
            >
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
                  checked={tsForm.tailscale_enabled}
                  onChange={(e) =>
                    setTsForm((f) => ({ ...f, tailscale_enabled: e.target.checked }))
                  }
                />
                Tailscale を有効にする
              </label>

              {/* 認証キー入力（書き込み専用） */}
              <Field label="認証キー（tskey-... 形式）">
                <div className={css({ display: "flex", flexDirection: "column", gap: "1" })}>
                  {cfg.tailscale_auth_key_set ? (
                    <div
                      className={css({
                        display: "flex",
                        alignItems: "center",
                        gap: "2",
                        fontSize: "sm",
                        color: "text.muted",
                        mb: "1",
                      })}
                    >
                      <KeyRound size={14} />
                      <span>設定済み（新しいキーを入力すると上書きされます）</span>
                    </div>
                  ) : (
                    <div
                      className={css({
                        display: "flex",
                        alignItems: "center",
                        gap: "2",
                        fontSize: "sm",
                        color: "text.subtle",
                        mb: "1",
                      })}
                    >
                      <KeyRound size={14} />
                      <span>未設定</span>
                    </div>
                  )}
                  <input
                    className={cx(input(), css({ width: "100%" }))}
                    type="password"
                    value={tsForm.tailscale_auth_key}
                    onChange={(e) =>
                      setTsForm((f) => ({ ...f, tailscale_auth_key: e.target.value }))
                    }
                    placeholder="tskey-..."
                    autoComplete="new-password"
                  />
                  <p className={css({ fontSize: "sm", color: "text.subtle" })}>
                    入力した認証キーは暗号化して保存されます。保存後は表示できません。
                    空欄のまま保存すると既存のキーを保持します。
                  </p>
                </div>
              </Field>

              <div className={css({ display: "flex", justifyContent: "flex-end" })}>
                <button
                  type="submit"
                  className={button({ variant: "primary" })}
                  style={{ height: "36px" }}
                  disabled={saveTsMutation.isPending}
                >
                  {saveTsMutation.isPending ? "保存中…" : "Tailscale を保存"}
                </button>
              </div>
            </form>
          </div>
        </SectionCard>

      </div>
    </PageLayout>
  );
}
