import React from "react";
import {
  LayoutDashboard,
  Phone,
  Network,
  Route,
  Boxes,
  Bot,
  BookUser,
  History,
  GitBranch,
  Wifi,
  Globe,
  Smartphone,
  Users,
  ServerCog,
  ScrollText,
  KeyRound,
  ShieldCheck,
  Settings2,
} from "lucide-react";

/** サイドナビとヘッダ題で共有するルート定義。 */
export interface NavItem {
  path: string;
  label: string;
  icon: React.ReactNode;
}

/** サイドナビのカテゴリ。title が null のセクションは見出しなしで描画する。 */
export interface NavSection {
  title: string | null;
  items: NavItem[];
}

const ICON_SIZE = 20;

export const NAV_SECTIONS: NavSection[] = [
  {
    title: null,
    items: [
      { path: "/", label: "ダッシュボード", icon: React.createElement(LayoutDashboard, { size: ICON_SIZE }) },
    ],
  },
  {
    title: "回線",
    items: [
      { path: "/extensions", label: "内線",            icon: React.createElement(Phone,     { size: ICON_SIZE }) },
      { path: "/trunks",     label: "外線トランク",    icon: React.createElement(Network,   { size: ICON_SIZE }) },
      { path: "/routes",     label: "ルーティング",    icon: React.createElement(Route,     { size: ICON_SIZE }) },
      { path: "/ai-agents",  label: "AI エージェント", icon: React.createElement(Bot,       { size: ICON_SIZE }) },
      { path: "/workflows",  label: "ワークフロー",    icon: React.createElement(GitBranch, { size: ICON_SIZE }) },
      { path: "/contacts",   label: "電話帳",          icon: React.createElement(BookUser,  { size: ICON_SIZE }) },
    ],
  },
  {
    title: "設定",
    items: [
      { path: "/providers", label: "プロバイダ",   icon: React.createElement(Boxes,      { size: ICON_SIZE }) },
      { path: "/devices",   label: "デバイス",     icon: React.createElement(Smartphone, { size: ICON_SIZE }) },
      // ネットワークは「内向き」（電話管理用 LAN 側）と「外向き」（リモートアクセス側）に分離
      { path: "/network",        label: "ネットワーク（内向き）", icon: React.createElement(Wifi,  { size: ICON_SIZE }) },
      { path: "/network/remote", label: "ネットワーク（外向き）", icon: React.createElement(Globe, { size: ICON_SIZE }) },
      { path: "/users",     label: "ユーザー管理", icon: React.createElement(Users,      { size: ICON_SIZE }) },
      { path: "/system",    label: "システム",     icon: React.createElement(ServerCog,  { size: ICON_SIZE }) },
      { path: "/sso",       label: "SSO / SCIM",   icon: React.createElement(KeyRound,   { size: ICON_SIZE }) },
      { path: "/settings/security", label: "セキュリティ", icon: React.createElement(ShieldCheck, { size: ICON_SIZE }) },
      { path: "/settings", label: "設定", icon: React.createElement(Settings2, { size: ICON_SIZE }) },
    ],
  },
  {
    title: "監査",
    items: [
      { path: "/cdr",   label: "通話履歴", icon: React.createElement(History,    { size: ICON_SIZE }) },
      { path: "/audit", label: "監査ログ", icon: React.createElement(ScrollText, { size: ICON_SIZE }) },
    ],
  },
];

/** 全セクションをフラットに並べた一覧（titleForPath などの探索用）。 */
export const NAV_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);

/**
 * 一般ユーザー（role=user）にも表示するパスの一覧。
 * バックエンドの管理 API はすべて require_admin で保護されているため、
 * 一般ユーザーには自分のアカウント関連（2FA 設定など）だけを見せる。
 */
export const USER_ALLOWED_PATHS: readonly string[] = ["/settings/security"];

/**
 * ロールに応じたサイドナビのセクションを返す。
 * - admin: 全セクション（従来どおり）
 * - それ以外（user など未知のロールを含む）: アカウント関連のみ。
 *   管理系項目は一切表示しない（安全側デフォルト）。
 */
export function navSectionsForRole(role: string): NavSection[] {
  if (role === "admin") return NAV_SECTIONS;
  const allowed = new Set(USER_ALLOWED_PATHS);
  const items = NAV_ITEMS.filter((i) => allowed.has(i.path));
  return items.length > 0 ? [{ title: "アカウント", items }] : [];
}

/** パスから画面題を引く（ヘッダ用）。プレフィックスは最長一致を優先する。 */
export function titleForPath(pathname: string): string {
  const exact = NAV_ITEMS.find((n) => n.path === pathname);
  if (exact) return exact.label;
  const prefix = NAV_ITEMS.filter(
    (n) => n.path !== "/" && pathname.startsWith(n.path),
  ).sort((a, b) => b.path.length - a.path.length)[0];
  return prefix?.label ?? "millicall";
}

/**
 * 現在のパスに対応するアクティブなナビ項目のパスを返す（サイドナビ強調用）。
 * /network と /network/remote のような入れ子パスでも最長一致の 1 件だけを返す。
 */
export function activeNavPath(pathname: string): string | null {
  let best: string | null = null;
  for (const { path } of NAV_ITEMS) {
    const hit =
      path === "/"
        ? pathname === "/"
        : pathname === path || pathname.startsWith(`${path}/`);
    if (hit && (best === null || path.length > best.length)) best = path;
  }
  return best;
}
