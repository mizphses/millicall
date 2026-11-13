import { describe, expect, it } from "vitest";
import { dateBoundary, parsePage, resultRange } from "../pages/cdr/historyFilters";

describe("履歴のページと期間", () => {
  it("空の結果を負の範囲にせず0件と表示する", () => {
    expect(resultRange(0, 0)).toBe("0 件");
    expect(resultRange(50, 0)).toBe("0 件");
    expect(resultRange(50, 3)).toBe("51–53 件目");
  });
  it("不正なURLページを先頭に戻す", () => {
    for (const page of ["", "-1", "0", "NaN", "1.5", "1000001"]) expect(parsePage(page)).toBe(1);
    expect(parsePage("2")).toBe(2);
  });
  it("終了日をローカル翌日0時の排他的境界に変換する", () => {
    expect(dateBoundary("2026-11-10")).toBe(new Date(2026, 10, 10).toISOString());
    expect(dateBoundary("2026-11-10", true)).toBe(new Date(2026, 10, 11).toISOString());
    expect(dateBoundary("invalid")).toBeUndefined();
  });
});
