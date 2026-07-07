// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createCsrfMiddleware,
  createUnauthorizedMiddleware,
  getCsrfCookie,
  LOGIN_PATH,
} from "../api/client";

function makeCtx(url: string, status: number) {
  return {
    request: new Request(url),
    response: new Response(null, { status }),
    // openapi-fetch は他のフィールドも渡すが、ミドルウェアが参照するのは request/response のみ
  } as unknown as Parameters<NonNullable<ReturnType<typeof createUnauthorizedMiddleware>["onResponse"]>>[0];
}

/** CSRF ミドルウェアの onRequest を呼び出すヘルパー。 */
async function runCsrf(method: string, url = "http://localhost/api/extensions") {
  const mw = createCsrfMiddleware();
  const request = new Request(url, { method });
  const ctx = { request } as unknown as Parameters<
    NonNullable<ReturnType<typeof createCsrfMiddleware>["onRequest"]>
  >[0];
  return mw.onRequest!(ctx);
}

/** document.cookie をクリアするヘルパー。 */
function clearCookies() {
  document.cookie.split(";").forEach((c) => {
    const name = c.split("=")[0].trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
  });
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

  it("2 段階ログイン（/api/auth/login/totp）の 401 ではリダイレクトしない", async () => {
    const redirect = vi.fn();
    const mw = createUnauthorizedMiddleware(redirect);
    await mw.onResponse!(makeCtx("http://localhost/api/auth/login/totp", 401));
    expect(redirect).not.toHaveBeenCalled();
  });
});

describe("createCsrfMiddleware", () => {
  afterEach(() => clearCookies());

  it("POST では Cookie の値を X-CSRF-Token ヘッダに載せる", async () => {
    document.cookie = "millicall_csrf=abc123";
    const req = (await runCsrf("POST")) as Request;
    expect(req.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("PUT / PATCH / DELETE でもヘッダを付与する", async () => {
    document.cookie = "millicall_csrf=tok";
    for (const m of ["PUT", "PATCH", "DELETE"]) {
      const req = (await runCsrf(m)) as Request;
      expect(req.headers.get("X-CSRF-Token")).toBe("tok");
    }
  });

  it("GET では X-CSRF-Token を付与しない", async () => {
    document.cookie = "millicall_csrf=abc123";
    const req = (await runCsrf("GET")) as Request;
    expect(req.headers.get("X-CSRF-Token")).toBeNull();
  });

  it("HEAD / OPTIONS でも付与しない", async () => {
    document.cookie = "millicall_csrf=abc123";
    for (const m of ["HEAD", "OPTIONS"]) {
      const req = (await runCsrf(m)) as Request;
      expect(req.headers.get("X-CSRF-Token")).toBeNull();
    }
  });

  it("Cookie が無い POST ではヘッダを付与しない（サーバー側で 403 になる）", async () => {
    clearCookies();
    const req = (await runCsrf("POST")) as Request;
    expect(req.headers.get("X-CSRF-Token")).toBeNull();
  });
});

describe("getCsrfCookie", () => {
  afterEach(() => clearCookies());

  it("複数 Cookie の中から millicall_csrf を抽出する", () => {
    document.cookie = "other=1";
    document.cookie = "millicall_csrf=xyz789";
    expect(getCsrfCookie()).toBe("xyz789");
  });

  it("URL エンコードされた値をデコードする", () => {
    document.cookie = "millicall_csrf=" + encodeURIComponent("a b+c");
    expect(getCsrfCookie()).toBe("a b+c");
  });

  it("Cookie が無ければ null", () => {
    clearCookies();
    expect(getCsrfCookie()).toBeNull();
  });
});
