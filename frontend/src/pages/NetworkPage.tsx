/**
 * ネットワーク設定ページ（Phase 5 Task 4）。
 *
 * セクション:
 *   (a) LAN / DHCP 設定
 *   (b) NAT / WAN 設定
 *   (c) 設定を適用ボタン → POST /api/network/apply
 *   (d) Tailscale VPN（有効化トグル + 認証キー入力 + 接続/切断 + ステータス）
 *
 * 認証キーは書き込み専用。tailscale_auth_key_set=true のときは「設定済み」と表示し、
 * 平文は絶対に表示しない。
 *
 * デザイン原則:
 *   - PandaCSS css()/cx() + styled-system/recipes (panel, button, input, badge)
 *   - lucide-react アイコン（サイズは明示的な px 値）
 *   - 既存の ProvidersPage / RoutesPage のハウススタイルに準拠
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import {
  Server,
  Globe,
  Shield,
  CheckCircle2,
  XCircle,
  Loader2,
  RefreshCw,
  Play,
  Square,
  KeyRound,
  Network,
} from "lucide-react";

import { css, cx } from "styled-system/css";
import { button, input, panel } from "styled-system/recipes";

import { api } from "../api/client";
import {
  NETWORK_CONFIG_KEY,
  NETWORK_TAILSCALE_STATUS_KEY,
} from "../queryKeys";
import { PageLayout } from "../components/PageLayout";
import { useToast } from "../toast/ToastProvider";

// ---------------------------------------------------------------------------
// API 型（schema.d.ts から自動生成される型を参照）
// ---------------------------------------------------------------------------

type NetworkConfigRead = {
  id: number;
  lan_interface: string;
  lan_ip: string;
  lan_prefix: number;
  dhcp_range_start: string;
  dhcp_range_end: string;
  dhcp_lease_hours: number;
  provisioning_base_url: string;
  nat_enabled: boolean;
  wan_interface: string;
  tailscale_enabled: boolean;
  tailscale_auth_key_set: boolean;
  created_at: string;
  updated_at: string;
};

type NetworkConfigUpdate = {
  lan_interface: string;
  lan_ip: string;
  lan_prefix: number;
  dhcp_range_start: string;
  dhcp_range_end: string;
  dhcp_lease_hours: number;
  provisioning_base_url: string;
  nat_enabled: boolean;
  wan_interface: string;
  tailscale_enabled: boolean;
  tailscale_auth_key?: string | null;
};

// ---------------------------------------------------------------------------
// API 関数
// ---------------------------------------------------------------------------

async function fetchNetworkConfig(): Promise<NetworkConfigRead> {
  const { data, error } = await api.GET("/api/network");
  if (error || !data) throw new Error("ネットワーク設定の取得に失敗しました");
  return data as NetworkConfigRead;
}

async function updateNetworkConfig(payload: NetworkConfigUpdate): Promise<NetworkConfigRead> {
  const { data, error } = await api.PUT("/api/network", { body: payload });
  if (error || !data) throw new Error("ネットワーク設定の保存に失敗しました");
  return data as NetworkConfigRead;
}

async function applyNetworkConfig(): Promise<void> {
  const { error, response } = await api.POST("/api/network/apply");
  if (response.status === 502) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      (body as { detail?: string }).detail ?? "netd への適用に失敗しました（502）"
    );
  }
  if (error) throw new Error("netd への適用に失敗しました");
}

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

type LanDhcpForm = {
  lan_interface: string;
  lan_ip: string;
  lan_prefix: string;
  dhcp_range_start: string;
  dhcp_range_end: string;
  dhcp_lease_hours: string;
  provisioning_base_url: string;
};

type NatForm = {
  nat_enabled: boolean;
  wan_interface: string;
};

type TailscaleForm = {
  tailscale_enabled: boolean;
  tailscale_auth_key: string;
};

function configToLanDhcpForm(cfg: NetworkConfigRead): LanDhcpForm {
  return {
    lan_interface: cfg.lan_interface,
    lan_ip: cfg.lan_ip,
    lan_prefix: String(cfg.lan_prefix),
    dhcp_range_start: cfg.dhcp_range_start,
    dhcp_range_end: cfg.dhcp_range_end,
    dhcp_lease_hours: String(cfg.dhcp_lease_hours),
    provisioning_base_url: cfg.provisioning_base_url,
  };
}

function configToNatForm(cfg: NetworkConfigRead): NatForm {
  return {
    nat_enabled: cfg.nat_enabled,
    wan_interface: cfg.wan_interface,
  };
}

// ---------------------------------------------------------------------------
// ページコンポーネント
// ---------------------------------------------------------------------------

export function NetworkPage() {
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

  // フォーム状態（LAN/DHCP と NAT は独立した Save ボタンを持つ）
  const [lanForm, setLanForm] = useState<LanDhcpForm>({
    lan_interface: "enp3s0",
    lan_ip: "172.20.0.1",
    lan_prefix: "16",
    dhcp_range_start: "172.20.1.1",
    dhcp_range_end: "172.20.254.254",
    dhcp_lease_hours: "12",
    provisioning_base_url: "",
  });

  const [natForm, setNatForm] = useState<NatForm>({
    nat_enabled: true,
    wan_interface: "",
  });

  const [tsForm, setTsForm] = useState<TailscaleForm>({
    tailscale_enabled: false,
    tailscale_auth_key: "",
  });

  // サーバーデータがロード済みになったらフォームを初期化する
  useEffect(() => {
    if (cfg) {
      setLanForm(configToLanDhcpForm(cfg));
      setNatForm(configToNatForm(cfg));
      setTsForm({
        tailscale_enabled: cfg.tailscale_enabled,
        tailscale_auth_key: "",
      });
    }
  }, [cfg]);

  // LAN/DHCP 保存
  const saveLanMutation = useMutation({
    mutationFn: () => {
      if (!cfg) throw new Error("設定データがロードされていません");
      const payload: NetworkConfigUpdate = {
        lan_interface: lanForm.lan_interface,
        lan_ip: lanForm.lan_ip,
        lan_prefix: parseInt(lanForm.lan_prefix, 10),
        dhcp_range_start: lanForm.dhcp_range_start,
        dhcp_range_end: lanForm.dhcp_range_end,
        dhcp_lease_hours: parseInt(lanForm.dhcp_lease_hours, 10),
        provisioning_base_url: lanForm.provisioning_base_url,
        nat_enabled: cfg.nat_enabled,
        wan_interface: cfg.wan_interface,
        tailscale_enabled: cfg.tailscale_enabled,
      };
      return updateNetworkConfig(payload);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(NETWORK_CONFIG_KEY, data);
      toast.success("LAN / DHCP 設定を保存しました");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "保存に失敗しました");
    },
  });

  // NAT 保存
  const saveNatMutation = useMutation({
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
        nat_enabled: natForm.nat_enabled,
        wan_interface: natForm.wan_interface,
        tailscale_enabled: cfg.tailscale_enabled,
      };
      return updateNetworkConfig(payload);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(NETWORK_CONFIG_KEY, data);
      toast.success("NAT 設定を保存しました");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "NAT 設定の保存に失敗しました");
    },
  });

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

  // 設定の適用
  const applyMutation = useMutation({
    mutationFn: applyNetworkConfig,
    onSuccess: () => {
      toast.success("ネットワーク設定を適用しました");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "適用に失敗しました");
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
      <PageLayout title="ネットワーク" description="LAN / DHCP / NAT / Tailscale の設定">
        <p className={css({ color: "text.muted", py: "6" })}>読み込み中…</p>
      </PageLayout>
    );
  }

  if (configQuery.isError || !cfg) {
    return (
      <PageLayout title="ネットワーク" description="LAN / DHCP / NAT / Tailscale の設定">
        <p className={css({ color: "danger.text", py: "6" })}>
          ネットワーク設定の取得に失敗しました。ページを再読み込みしてください。
        </p>
      </PageLayout>
    );
  }

  const tailscaleStatus = statusQuery.data;
  const isConnected = tailscaleStatus?.connected ?? false;

  return (
    <PageLayout title="ネットワーク" description="LAN / DHCP / NAT / Tailscale の設定">
      <div className={css({ display: "flex", flexDirection: "column", gap: "6" })}>

        {/* ─── LAN / DHCP セクション ─── */}
        <SectionCard
          icon={<Server size={18} />}
          title="LAN / DHCP"
          description="LAN インタフェース、IP アドレス、DHCP 払い出し範囲を設定します"
        >
          <form
            id="lan-dhcp-form"
            onSubmit={(e) => {
              e.preventDefault();
              saveLanMutation.mutate();
            }}
            className={css({ display: "flex", flexDirection: "column", gap: "4" })}
          >
            <div
              className={css({
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
                gap: "4",
              })}
            >
              <Field label="LAN インタフェース">
                <input
                  className={input()}
                  value={lanForm.lan_interface}
                  onChange={(e) => setLanForm((f) => ({ ...f, lan_interface: e.target.value }))}
                  placeholder="enp3s0"
                />
              </Field>
              <Field label="LAN IP アドレス">
                <input
                  className={input()}
                  value={lanForm.lan_ip}
                  onChange={(e) => setLanForm((f) => ({ ...f, lan_ip: e.target.value }))}
                  placeholder="172.20.0.1"
                />
              </Field>
              <Field label="CIDR プレフィックス長">
                <input
                  className={input()}
                  type="number"
                  min={0}
                  max={32}
                  value={lanForm.lan_prefix}
                  onChange={(e) => setLanForm((f) => ({ ...f, lan_prefix: e.target.value }))}
                  placeholder="16"
                />
              </Field>
              <Field label="DHCP リース時間（時間）">
                <input
                  className={input()}
                  type="number"
                  min={1}
                  max={720}
                  value={lanForm.dhcp_lease_hours}
                  onChange={(e) =>
                    setLanForm((f) => ({ ...f, dhcp_lease_hours: e.target.value }))
                  }
                  placeholder="12"
                />
              </Field>
              <Field label="DHCP 開始アドレス">
                <input
                  className={input()}
                  value={lanForm.dhcp_range_start}
                  onChange={(e) => setLanForm((f) => ({ ...f, dhcp_range_start: e.target.value }))}
                  placeholder="172.20.1.1"
                />
              </Field>
              <Field label="DHCP 終了アドレス">
                <input
                  className={input()}
                  value={lanForm.dhcp_range_end}
                  onChange={(e) => setLanForm((f) => ({ ...f, dhcp_range_end: e.target.value }))}
                  placeholder="172.20.254.254"
                />
              </Field>
            </div>
            <Field label="プロビジョニング URL（空の場合は自動生成）">
              <input
                className={cx(input(), css({ width: "100%" }))}
                value={lanForm.provisioning_base_url}
                onChange={(e) =>
                  setLanForm((f) => ({ ...f, provisioning_base_url: e.target.value }))
                }
                placeholder="空のままにすると http://<LAN IP>:8000/provisioning/ を使用します"
              />
            </Field>
            <div className={css({ display: "flex", justifyContent: "flex-end" })}>
              <button
                type="submit"
                className={button({ variant: "primary" })}
                style={{ height: "36px" }}
                disabled={saveLanMutation.isPending}
              >
                {saveLanMutation.isPending ? "保存中…" : "LAN / DHCP を保存"}
              </button>
            </div>
          </form>
        </SectionCard>

        {/* ─── NAT セクション ─── */}
        <SectionCard
          icon={<Globe size={18} />}
          title="NAT / WAN"
          description="インターネット向けの NAT（マスカレード）と WAN インタフェースを設定します"
        >
          <form
            id="nat-form"
            onSubmit={(e) => {
              e.preventDefault();
              saveNatMutation.mutate();
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
                checked={natForm.nat_enabled}
                onChange={(e) => setNatForm((f) => ({ ...f, nat_enabled: e.target.checked }))}
              />
              NAT（マスカレード）を有効にする
            </label>

            <Field label="WAN インタフェース（NAT 有効時に必要）">
              <input
                className={input()}
                value={natForm.wan_interface}
                onChange={(e) => setNatForm((f) => ({ ...f, wan_interface: e.target.value }))}
                placeholder="enp2s0"
                disabled={!natForm.nat_enabled}
              />
            </Field>

            <div className={css({ display: "flex", justifyContent: "flex-end" })}>
              <button
                type="submit"
                className={button({ variant: "primary" })}
                style={{ height: "36px" }}
                disabled={saveNatMutation.isPending}
              >
                {saveNatMutation.isPending ? "保存中…" : "NAT を保存"}
              </button>
            </div>
          </form>
        </SectionCard>

        {/* ─── 適用ボタン ─── */}
        <div
          className={cx(
            panel(),
            css({
              p: "4",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "4",
            }),
          )}
        >
          <div>
            <p className={css({ fontWeight: "600", fontSize: "md" })}>ネットワーク設定を適用</p>
            <p className={css({ fontSize: "sm", color: "text.muted", mt: "1" })}>
              保存した設定を実際のホストへ反映します。DHCP と NAT の変更を同時に適用します。
            </p>
          </div>
          <button
            type="button"
            className={button({ variant: "primary" })}
            style={{ height: "36px", flexShrink: 0 }}
            onClick={() => applyMutation.mutate()}
            disabled={applyMutation.isPending}
          >
            <Network size={16} />
            {applyMutation.isPending ? "適用中…" : "設定を適用"}
          </button>
        </div>

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

// ---------------------------------------------------------------------------
// 補助コンポーネント
// ---------------------------------------------------------------------------

/** セクションカード: アイコン + タイトル + 説明 + コンテンツ。 */
function SectionCard({
  icon,
  title,
  description,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cx(panel(), css({ p: "5", display: "flex", flexDirection: "column", gap: "4" }))}>
      <div className={css({ display: "flex", alignItems: "center", gap: "2", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "border", pb: "3" })}>
        <span className={css({ color: "accent" })}>{icon}</span>
        <div>
          <h2 className={css({ fontWeight: "600", fontSize: "md" })}>{title}</h2>
          <p className={css({ fontSize: "sm", color: "text.muted" })}>{description}</p>
        </div>
      </div>
      <div>{children}</div>
    </div>
  );
}

/** フォーム 1 項目（ラベル + 入力）。ProvidersPage / RoutesPage と同じパターン。 */
function Field({
  label,
  children,
}: {
  label: string;
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
    </div>
  );
}

