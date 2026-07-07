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
  Smartphone,
  Users,
  ServerCog,
  ScrollText,
  KeyRound,
  ShieldCheck,
} from "lucide-react";

/** サイドナビとヘッダ題で共有するルート定義。 */
export interface NavItem {
  path: string;
  label: string;
  icon: React.ReactNode;
}

const ICON_SIZE = 20;

export const NAV_ITEMS: NavItem[] = [
  { path: "/",           label: "ダッシュボード",   icon: React.createElement(LayoutDashboard, { size: ICON_SIZE }) },
  { path: "/extensions", label: "内線",             icon: React.createElement(Phone,           { size: ICON_SIZE }) },
  { path: "/trunks",     label: "外線トランク",     icon: React.createElement(Network,         { size: ICON_SIZE }) },
  { path: "/routes",     label: "ルーティング",     icon: React.createElement(Route,           { size: ICON_SIZE }) },
  { path: "/providers",  label: "プロバイダ",       icon: React.createElement(Boxes,           { size: ICON_SIZE }) },
  { path: "/ai-agents",  label: "AI エージェント",  icon: React.createElement(Bot,             { size: ICON_SIZE }) },
  { path: "/contacts",   label: "電話帳",           icon: React.createElement(BookUser,        { size: ICON_SIZE }) },
  { path: "/cdr",        label: "通話履歴",         icon: React.createElement(History,         { size: ICON_SIZE }) },
  { path: "/workflows",  label: "ワークフロー",     icon: React.createElement(GitBranch,       { size: ICON_SIZE }) },
  { path: "/network",   label: "ネットワーク",     icon: React.createElement(Wifi,            { size: ICON_SIZE }) },
  { path: "/devices",   label: "デバイス",         icon: React.createElement(Smartphone,       { size: ICON_SIZE }) },
  { path: "/users",     label: "ユーザー管理",     icon: React.createElement(Users,           { size: ICON_SIZE }) },
  { path: "/system",    label: "システム",         icon: React.createElement(ServerCog,       { size: ICON_SIZE }) },
  { path: "/audit",     label: "監査ログ",         icon: React.createElement(ScrollText,      { size: ICON_SIZE }) },
  { path: "/sso",       label: "SSO / SCIM",       icon: React.createElement(KeyRound,        { size: ICON_SIZE }) },
  { path: "/settings/security", label: "セキュリティ", icon: React.createElement(ShieldCheck,  { size: ICON_SIZE }) },
];

/** パスから画面題を引く（ヘッダ用）。 */
export function titleForPath(pathname: string): string {
  const exact = NAV_ITEMS.find((n) => n.path === pathname);
  if (exact) return exact.label;
  const prefix = NAV_ITEMS.filter((n) => n.path !== "/").find((n) =>
    pathname.startsWith(n.path),
  );
  return prefix?.label ?? "millicall";
}
