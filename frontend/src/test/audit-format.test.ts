import { describe, expect, it } from "vitest";

import {
  canNext,
  canPrev,
  formatTarget,
  formatTimestamp,
  stepOffset,
  type AuditLogRead,
} from "../pages/audit/format";

const base: AuditLogRead = {
  id: 1,
  action: "user.create",
  actor_label: "admin",
  actor_user_id: 1,
  created_at: "2026-07-07T12:34:56Z",
  detail: "created bob",
  ip_address: "10.0.0.1",
  target_id: "42",
  target_type: "user",
};

describe("formatTimestamp", () => {
  it("ISO を YYYY-MM-DD HH:mm:ss に整形する", () => {
    // ローカルタイムゾーン依存を避け、形式だけ検証
    expect(formatTimestamp(base.created_at)).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
  });

  it("不正な日時はそのまま返す", () => {
    expect(formatTimestamp("not-a-date")).toBe("not-a-date");
  });
});

describe("formatTarget", () => {
  it("type と id を type#id で結合する", () => {
    expect(formatTarget(base)).toBe("user#42");
  });

  it("id のみ / type のみ / 両方なしを扱う", () => {
    expect(formatTarget({ ...base, target_id: null })).toBe("user");
    expect(formatTarget({ ...base, target_type: null })).toBe("42");
    expect(formatTarget({ ...base, target_type: null, target_id: null })).toBe("—");
  });
});

describe("ページネーション", () => {
  it("canPrev は offset>0 のとき true", () => {
    expect(canPrev(0)).toBe(false);
    expect(canPrev(50)).toBe(true);
  });

  it("canNext は取得件数が limit 以上のとき true", () => {
    expect(canNext(50, 50)).toBe(true);
    expect(canNext(30, 50)).toBe(false);
  });

  it("stepOffset は limit 単位で動き、0 未満にならない", () => {
    expect(stepOffset(0, 50, 1)).toBe(50);
    expect(stepOffset(50, 50, -1)).toBe(0);
    expect(stepOffset(0, 50, -1)).toBe(0);
  });
});
