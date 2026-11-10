import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  lazyRouteComponent,
  redirect,
  type RouterHistory,
} from "@tanstack/react-router";

import { fetchCurrentUser, fetchLoginConfig, USER_HOME_PATH } from "../auth/auth";
import type { CurrentUser } from "../auth/auth";
import { RouteError, RoutePending } from "./RouteStatus";

// 各ページはルート単位で別チャンクに分割する（Issue #82）。
// beforeLoad（認証ガード）はルート定義側に残るため、未認証ユーザーは
// 保護ページのチャンクを取得する前に /login へリダイレクトされる。
// lazyRouteComponent は router の preload（Link の intent）からも先読みされる。
const AppShell = lazyRouteComponent(() => import("../shell/AppShell"), "AppShell");
const LoginPage = lazyRouteComponent(() => import("../pages/LoginPage"), "LoginPage");
const DashboardPage = lazyRouteComponent(() => import("../pages/DashboardPage"), "DashboardPage");
const ExtensionsPage = lazyRouteComponent(
  () => import("../pages/ExtensionsPage"),
  "ExtensionsPage",
);
const TrunksPage = lazyRouteComponent(() => import("../pages/TrunksPage"), "TrunksPage");
const RoutesPage = lazyRouteComponent(() => import("../pages/RoutesPage"), "RoutesPage");
const ProvidersPage = lazyRouteComponent(() => import("../pages/ProvidersPage"), "ProvidersPage");
const AiAgentsPage = lazyRouteComponent(() => import("../pages/AiAgentsPage"), "AiAgentsPage");
const ContactsPage = lazyRouteComponent(() => import("../pages/ContactsPage"), "ContactsPage");
const CdrPage = lazyRouteComponent(() => import("../pages/CdrPage"), "CdrPage");
const WorkflowsPage = lazyRouteComponent(() => import("../pages/WorkflowsPage"), "WorkflowsPage");
const WorkflowEditorPage = lazyRouteComponent(
  () => import("../pages/WorkflowEditorPage"),
  "WorkflowEditorPage",
);
const NetworkPage = lazyRouteComponent(() => import("../pages/NetworkPage"), "NetworkPage");
const NetworkRemotePage = lazyRouteComponent(
  () => import("../pages/NetworkRemotePage"),
  "NetworkRemotePage",
);
const DevicesPage = lazyRouteComponent(() => import("../pages/DevicesPage"), "DevicesPage");
const UsersPage = lazyRouteComponent(() => import("../pages/UsersPage"), "UsersPage");
const SecurityPage = lazyRouteComponent(() => import("../pages/SecurityPage"), "SecurityPage");
const SystemPage = lazyRouteComponent(() => import("../pages/SystemPage"), "SystemPage");
const AuditPage = lazyRouteComponent(() => import("../pages/AuditPage"), "AuditPage");
const SsoPage = lazyRouteComponent(() => import("../pages/SsoPage"), "SsoPage");
const SettingsPage = lazyRouteComponent(() => import("../pages/SettingsPage"), "SettingsPage");

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
});

/**
 * 認証済み領域のレイアウトルート。
 * /login 以外へのアクセス時、GET /api/auth/me で認証を確認し、
 * 未認証なら /login へリダイレクトする（横断ガード）。
 */
const authLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "authenticated",
  beforeLoad: async (): Promise<{ user: CurrentUser }> => {
    const user = await fetchCurrentUser();
    if (!user) {
      throw redirect({ to: "/login" });
    }
    return { user };
  },
  component: AuthenticatedLayout,
});

function AuthenticatedLayout() {
  const { user } = authLayoutRoute.useRouteContext();
  return <AppShell username={user.display_name || user.username} role={user.role} />;
}

/**
 * admin 専用領域のレイアウトルート（パスなし）。
 * 直接 URL を叩かれた場合も、admin 以外はアカウントページへリダイレクトする。
 * バックエンド API は require_admin で保護済みだが、UI 側でも 403 画面を見せない。
 */
const adminLayoutRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  id: "admin",
  beforeLoad: async ({ context }) => {
    if (context.user.role !== "admin") {
      throw redirect({ to: USER_HOME_PATH });
    }
    if (!context.user.totp_enabled && (await fetchLoginConfig()).totp_required) {
      throw redirect({ to: USER_HOME_PATH });
    }
  },
});

const dashboardRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/",
  component: DashboardPage,
});

const extensionsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/extensions",
  component: ExtensionsPage,
});

const trunksRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/trunks",
  component: TrunksPage,
});

const routesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/routes",
  component: RoutesPage,
});

const providersRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/providers",
  component: ProvidersPage,
});

const aiAgentsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/ai-agents",
  component: AiAgentsPage,
});

const contactsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/contacts",
  component: ContactsPage,
});

const cdrRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/cdr",
  component: CdrPage,
});

const workflowsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/workflows",
  component: WorkflowsPage,
});

const workflowEditorRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/workflows/$workflowId",
  component: WorkflowEditorPage,
});

// ネットワーク（内向き）: 電話管理用 LAN 側（LAN / DHCP / NAT）。
// 従来の /network パスを互換のため維持する。
const networkRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/network",
  component: NetworkPage,
});

// ネットワーク（外向き）: リモートアクセス側（Tailscale など）。
const networkRemoteRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/network/remote",
  component: NetworkRemotePage,
});

const devicesRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/devices",
  component: DevicesPage,
});

// ─── Phase 6 認証強化ページ（T9b） ───

const usersRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/users",
  component: UsersPage,
});

// セキュリティ（2FA 自己設定）は一般ユーザーにも開放するため
// admin レイアウトの外（認証済みレイアウト直下）に置く。
const securityRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/settings/security",
  component: SecurityPage,
});

const systemRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/system",
  component: SystemPage,
});

const auditRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/audit",
  component: AuditPage,
});

const ssoRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/sso",
  component: SsoPage,
});

const settingsRoute = createRoute({
  getParentRoute: () => adminLayoutRoute,
  path: "/settings",
  component: SettingsPage,
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  authLayoutRoute.addChildren([
    securityRoute,
    adminLayoutRoute.addChildren([
      dashboardRoute,
      extensionsRoute,
      trunksRoute,
      routesRoute,
      providersRoute,
      aiAgentsRoute,
      contactsRoute,
      cdrRoute,
      workflowsRoute,
      workflowEditorRoute,
      networkRoute,
      networkRemoteRoute,
      devicesRoute,
      usersRoute,
      systemRoute,
      auditRoute,
      ssoRoute,
      settingsRoute,
    ]),
  ]),
]);

/** テストで memory history を差し込めるよう、router 生成を関数に切り出している。 */
export function createAppRouter(history?: RouterHistory) {
  return createRouter({
    routeTree,
    history,
    defaultPreload: "intent",
    // チャンク取得が速い場合に pending 表示がちらつかないよう閾値を短めに保つ。
    defaultPendingMs: 200,
    defaultPendingMinMs: 300,
    defaultPendingComponent: RoutePending,
    defaultErrorComponent: RouteError,
  });
}

export const router = createAppRouter();

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
