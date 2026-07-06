// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

import { createUnauthorizedMiddleware, LOGIN_PATH } from "../api/client";

function makeCtx(url: string, status: number) {
  return {
    request: new Request(url),
    response: new Response(null, { status }),
    // openapi-fetch は他のフィールドも渡すが、ミドルウェアが参照するのは request/response のみ
  } as unknown as Parameters<NonNullable<ReturnType<typeof createUnauthorizedMiddleware>["onResponse"]>>[0];
}

describe("createUnauthorizedMiddleware", () => {
  it("通常 API の 401 でログインへリダイレクトする", async () => {
    const redirect = vi.fn();
    const mw = createUnauthorizedMiddleware(redirect);
    await mw.onResponse!(makeCtx("http://localhost/api/extensions", 401));
    expect(redirect).toHaveBeenCalledWith(LOGIN_PATH);
  });

  it("認証確認系（/api/auth/me, /api/auth/login）の 401 ではリダイレクトしない", async () => {
    const redirect = vi.fn();
    const mw = createUnauthorizedMiddleware(redirect);
    await mw.onResponse!(makeCtx("http://localhost/api/auth/me", 401));
    await mw.onResponse!(makeCtx("http://localhost/api/auth/login", 401));
    expect(redirect).not.toHaveBeenCalled();
  });

  it("2xx 応答ではリダイレクトしない", async () => {
    const redirect = vi.fn();
    const mw = createUnauthorizedMiddleware(redirect);
    await mw.onResponse!(makeCtx("http://localhost/api/extensions", 200));
    expect(redirect).not.toHaveBeenCalled();
  });
});
