import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from "@tanstack/react-router";

import { fetchCurrentUser } from "../auth/auth";
import type { CurrentUser } from "../auth/auth";
import { AppShell } from "../shell/AppShell";
import { LoginPage } from "../pages/LoginPage";
import { DashboardPage } from "../pages/DashboardPage";
import { ExtensionsPage } from "../pages/ExtensionsPage";
import { TrunksPage } from "../pages/TrunksPage";
import { RoutesPage } from "../pages/RoutesPage";
import { ProvidersPage } from "../pages/ProvidersPage";
import { AiAgentsPage } from "../pages/AiAgentsPage";
import { ContactsPage } from "../pages/ContactsPage";
import { CdrPage } from "../pages/CdrPage";
import { WorkflowsPage } from "../pages/WorkflowsPage";
import { WorkflowEditorPage } from "../pages/WorkflowEditorPage";

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
  return <AppShell username={user.display_name || user.username} />;
}

const dashboardRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/",
  component: DashboardPage,
});

const extensionsRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/extensions",
  component: ExtensionsPage,
});

const trunksRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/trunks",
  component: TrunksPage,
});

const routesRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/routes",
  component: RoutesPage,
});

const providersRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/providers",
  component: ProvidersPage,
});

const aiAgentsRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/ai-agents",
  component: AiAgentsPage,
});

const contactsRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/contacts",
  component: ContactsPage,
});

const cdrRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/cdr",
  component: CdrPage,
});

const workflowsRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/workflows",
  component: WorkflowsPage,
});

const workflowEditorRoute = createRoute({
  getParentRoute: () => authLayoutRoute,
  path: "/workflows/$workflowId",
  component: WorkflowEditorPage,
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  authLayoutRoute.addChildren([
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
  ]),
]);

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
