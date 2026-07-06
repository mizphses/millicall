import { describe, expect, it } from "vitest";

import { formatDuration, formatDateTime, formatLatency } from "../pages/cdr/format";

describe("formatDuration（秒 → mm:ss）", () => {
  it("60 秒未満はゼロ埋めの秒", () => {
    expect(formatDuration(0)).toBe("00:00");
    expect(formatDuration(5)).toBe("00:05");
    expect(formatDuration(30)).toBe("00:30");
  });

  it("分と秒に分解する", () => {
    expect(formatDuration(90)).toBe("01:30");
    expect(formatDuration(600)).toBe("10:00");
  });

  it("60 分以上は分が桁上がりする", () => {
    expect(formatDuration(3661)).toBe("61:01");
  });

  it("小数は切り捨てる", () => {
    expect(formatDuration(90.9)).toBe("01:30");
  });

  it("null / 負値 / 非数は 00:00", () => {
    expect(formatDuration(null)).toBe("00:00");
    expect(formatDuration(undefined)).toBe("00:00");
    expect(formatDuration(-5)).toBe("00:00");
    expect(formatDuration(Number.NaN)).toBe("00:00");
  });
});

describe("formatDateTime（ISO → ロケール日時）", () => {
  it("null / 空文字は '-'", () => {
    expect(formatDateTime(null)).toBe("-");
    expect(formatDateTime(undefined)).toBe("-");
    expect(formatDateTime("")).toBe("-");
  });

  it("パース不能な値はそのまま返す", () => {
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });

  it("有効な日時は年月日と時刻を含む文字列に整形する", () => {
    const out = formatDateTime("2026-07-05T10:05:03");
    expect(out).toContain("2026");
    expect(out).toContain("05");
    expect(out).toContain("03");
  });
});

describe("formatLatency", () => {
  it("数値に ms を付ける（四捨五入）", () => {
    expect(formatLatency(420)).toBe("420ms");
    expect(formatLatency(420.4)).toBe("420ms");
    expect(formatLatency(420.6)).toBe("421ms");
  });

  it("null / 非数は '-'", () => {
    expect(formatLatency(null)).toBe("-");
    expect(formatLatency(undefined)).toBe("-");
    expect(formatLatency(Number.NaN)).toBe("-");
  });
});
