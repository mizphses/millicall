/**
 * ネットワーク設定ページ共通モジュール。
 *
 * 「ネットワーク（内向き）」（LAN / DHCP / NAT）と
 * 「ネットワーク（外向き）」（Tailscale などリモートアクセス）の
 * 両ページで共有する API 型・API 関数・UI 部品を提供する。
 */

import { css, cx } from "styled-system/css";
import { panel } from "styled-system/recipes";

import { Children, cloneElement, isValidElement, useId } from "react";
import { apiError } from "../settings/formState";
import { api } from "../../api/client";

// ---------------------------------------------------------------------------
// API 型（schema.d.ts から自動生成される型を参照）
// ---------------------------------------------------------------------------

export type NetworkConfigRead = {
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

export type NetworkConfigUpdate = {
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
// API 関数（設定の取得/更新は両ページで共有）
// ---------------------------------------------------------------------------

export async function fetchNetworkConfig(): Promise<NetworkConfigRead> {
  const { data, error } = await api.GET("/api/network");
  if (error || !data) throw new Error("ネットワーク設定の取得に失敗しました");
  return data as NetworkConfigRead;
}

export async function updateNetworkConfig(
  payload: NetworkConfigUpdate,
): Promise<NetworkConfigRead> {
  const { data, error } = await api.PUT("/api/network", { body: payload });
  if (error || !data) throw apiError(error, "ネットワーク設定の保存に失敗しました");
  return data as NetworkConfigRead;
}

// ---------------------------------------------------------------------------
// 補助コンポーネント
// ---------------------------------------------------------------------------

/** セクションカード: アイコン + タイトル + 説明 + コンテンツ。 */
export function SectionCard({
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
export function Field({
  label,
  children,
  error,
}: {
  label: string;
  children: React.ReactNode;
  error?: string;
}) {
  const id = useId();
  function associate(node: React.ReactNode): React.ReactNode {
    return Children.map(node, (child) => {
      if (!isValidElement(child)) return child;
      const element = child as React.ReactElement<{ children?: React.ReactNode; id?: string; "aria-invalid"?: boolean; "aria-describedby"?: string }>;
      if (["input", "select", "textarea"].includes(String(element.type))) return cloneElement(element, { id, "aria-invalid": Boolean(error), "aria-describedby": error ? `${id}-error` : undefined });
      return element.props.children ? cloneElement(element, { children: associate(element.props.children) }) : element;
    });
  }
  const control = associate(children);
  return (
    <div>
      <label htmlFor={id} className={css({ display: "block" })}>
        <span
          className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}
        >
          {label}
        </span>
      </label>
      {control}
      {error && <p id={`${id}-error`} role="alert" className={css({ color: "danger.text", fontSize: "sm", mt: "1" })}>{error}</p>}
    </div>
  );
}
