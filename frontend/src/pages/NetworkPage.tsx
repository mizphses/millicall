/**
 * ネットワーク（内向き）設定ページ。
 *
 * 電話管理用の LAN 側機能（netd が管理する範囲）を扱う:
 *   (a) LAN / DHCP 設定（dnsmasq の DHCP/DNS）
 *   (b) NAT / WAN 設定（nftables の NAT）
 *   (c) 設定を適用ボタン → POST /api/network/apply
 *
 * リモートアクセス側（Tailscale など）は「ネットワーク（外向き）」
 * （NetworkRemotePage / /network/remote）に分離している。
 *
 * デザイン原則:
 *   - PandaCSS css()/cx() + styled-system/recipes (panel, button, input)
 *   - lucide-react アイコン（サイズは明示的な px 値）
 *   - 既存の ProvidersPage / RoutesPage のハウススタイルに準拠
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { Server, Globe, Network } from "lucide-react";

import { css, cx } from "styled-system/css";
import { button, input, panel } from "styled-system/recipes";

import { api } from "../api/client";
import { NETWORK_CONFIG_KEY } from "../queryKeys";
import { PageLayout } from "../components/PageLayout";
import { useToast } from "../toast/ToastProvider";
import {
  Field,
  SectionCard,
  fetchNetworkConfig,
  updateNetworkConfig,
  type NetworkConfigRead,
  type NetworkConfigUpdate,
} from "./network/shared";

// ---------------------------------------------------------------------------
// API 関数
// ---------------------------------------------------------------------------

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

const PAGE_TITLE = "ネットワーク（内向き）";
const PAGE_DESCRIPTION = "電話管理用 LAN 側の設定（LAN / DHCP / NAT）";

export function NetworkPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const configQuery = useQuery({
    queryKey: NETWORK_CONFIG_KEY,
    queryFn: fetchNetworkConfig,
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

  // サーバーデータがロード済みになったらフォームを初期化する
  useEffect(() => {
    if (cfg) {
      setLanForm(configToLanDhcpForm(cfg));
      setNatForm(configToNatForm(cfg));
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

  return (
    <PageLayout title={PAGE_TITLE} description={PAGE_DESCRIPTION}>
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

      </div>
    </PageLayout>
  );
}
