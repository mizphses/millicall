import { useRouter, useRouterState } from "@tanstack/react-router";

/** Collection state survives reloads, shared links and browser navigation. */
export function useUrlState(
  key: string,
  defaultValue = "",
): [
  string,
  (
    value: string,
    options?: { replace?: boolean; ignoreBlocker?: boolean },
  ) => void,
] {
  const router = useRouter();
  const search = useRouterState({
    select: (state) => state.location.search,
  }) as Record<string, unknown>;
  const value = search[key] == null ? defaultValue : String(search[key]);
  return [
    value,
    (next, options) => {
      const current = router.state.location;
      const updated: Record<string, unknown> = { ...current.search };
      if (!next || next === defaultValue) delete updated[key];
      else updated[key] = next;
      void router.navigate({
        to: current.pathname,
        search: updated as never,
        replace: options?.replace ?? true,
        ignoreBlocker: options?.ignoreBlocker,
      });
    },
  ];
}
