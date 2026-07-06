/** サイドナビとヘッダ題で共有するルート定義。 */
export interface NavItem {
  path: string;
  label: string;
  /** シンプルなアイコン（絵文字/記号）。フラットデザインに合わせ装飾は最小。 */
  icon: string;
}

export const NAV_ITEMS: NavItem[] = [
  { path: "/", label: "ダッシュボード", icon: "▦" },
  { path: "/extensions", label: "内線", icon: "☎" },
  { path: "/trunks", label: "外線トランク", icon: "🖧" },
  { path: "/routes", label: "ルーティング", icon: "⇄" },
  { path: "/providers", label: "プロバイダ", icon: "◈" },
  { path: "/ai-agents", label: "AI エージェント", icon: "🤖" },
  { path: "/contacts", label: "電話帳", icon: "▤" },
  { path: "/cdr", label: "通話履歴", icon: "⧗" },
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
