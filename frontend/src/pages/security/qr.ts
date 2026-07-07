/**
 * 極小の QR コード生成器（byte モード / EC レベル L / 自動バージョン選択）。
 *
 * otpauth:// URI を QR にするためだけの用途に絞った自前実装。
 * 外部依存を増やさないための最小構成。返り値は真偽値のモジュール行列
 * （true = 黒モジュール）。SVG 描画は呼び出し側で行う。
 *
 * 対応: バージョン 1〜10（byte モード / EC レベル L）。otpauth URI は
 * 通常 60〜120 文字程度で、この範囲に収まる。
 */

// ─── ガロア体 GF(256) テーブル ───
const EXP = new Array<number>(512);
const LOG = new Array<number>(256);
(function initGaloisField() {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    EXP[i] = x;
    LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
})();

function gfMul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0;
  return EXP[LOG[a] + LOG[b]];
}

/** 生成多項式を計算する。 */
function rsGeneratorPoly(degree: number): number[] {
  let poly = [1];
  for (let i = 0; i < degree; i++) {
    const next = new Array<number>(poly.length + 1).fill(0);
    for (let j = 0; j < poly.length; j++) {
      next[j] ^= gfMul(poly[j], 1);
      next[j + 1] ^= gfMul(poly[j], EXP[i]);
    }
    poly = next;
  }
  return poly;
}

/** Reed-Solomon 誤り訂正コードを計算する。 */
function rsEncode(data: number[], ecLen: number): number[] {
  const gen = rsGeneratorPoly(ecLen);
  const res = new Array<number>(ecLen).fill(0);
  for (const d of data) {
    const factor = d ^ res[0];
    res.shift();
    res.push(0);
    for (let i = 0; i < res.length; i++) {
      res[i] ^= gfMul(gen[i + 1], factor);
    }
  }
  return res;
}

// ─── バージョン別容量（byte モード / EC レベル L） ───
// [version]: { totalCodewords, ecPerBlock, blocks, capacityBytes }
// 単一ブロックで済むバージョン 1〜9 と、2 ブロックの 10 を扱う。
type VersionSpec = {
  version: number;
  size: number;
  totalDataCodewords: number;
  ecPerBlock: number;
  blocks: { count: number; dataCodewords: number }[];
  capacityBytes: number;
  alignment: number[];
};

// EC レベル L の仕様（QR 標準表より抜粋）。
const VERSIONS: VersionSpec[] = [
  { version: 1, size: 21, totalDataCodewords: 19, ecPerBlock: 7, blocks: [{ count: 1, dataCodewords: 19 }], capacityBytes: 17, alignment: [] },
  { version: 2, size: 25, totalDataCodewords: 34, ecPerBlock: 10, blocks: [{ count: 1, dataCodewords: 34 }], capacityBytes: 32, alignment: [6, 18] },
  { version: 3, size: 29, totalDataCodewords: 55, ecPerBlock: 15, blocks: [{ count: 1, dataCodewords: 55 }], capacityBytes: 53, alignment: [6, 22] },
  { version: 4, size: 33, totalDataCodewords: 80, ecPerBlock: 20, blocks: [{ count: 1, dataCodewords: 80 }], capacityBytes: 78, alignment: [6, 26] },
  { version: 5, size: 37, totalDataCodewords: 108, ecPerBlock: 26, blocks: [{ count: 1, dataCodewords: 108 }], capacityBytes: 106, alignment: [6, 30] },
  { version: 6, size: 41, totalDataCodewords: 136, ecPerBlock: 18, blocks: [{ count: 2, dataCodewords: 68 }], capacityBytes: 134, alignment: [6, 34] },
  { version: 7, size: 45, totalDataCodewords: 156, ecPerBlock: 20, blocks: [{ count: 2, dataCodewords: 78 }], capacityBytes: 154, alignment: [6, 22, 38] },
  { version: 8, size: 49, totalDataCodewords: 194, ecPerBlock: 24, blocks: [{ count: 2, dataCodewords: 97 }], capacityBytes: 192, alignment: [6, 24, 42] },
  { version: 9, size: 53, totalDataCodewords: 232, ecPerBlock: 30, blocks: [{ count: 2, dataCodewords: 116 }], capacityBytes: 230, alignment: [6, 26, 46] },
  { version: 10, size: 57, totalDataCodewords: 274, ecPerBlock: 18, blocks: [{ count: 2, dataCodewords: 68 }, { count: 2, dataCodewords: 69 }], capacityBytes: 271, alignment: [6, 28, 50] },
];

function pickVersion(byteLen: number): VersionSpec | null {
  for (const v of VERSIONS) {
    // 文字数カウント (byte モード, v1-9 は 8bit) + モード指示子 4bit を考慮した概算
    const charCountBits = v.version <= 9 ? 8 : 16;
    const dataBits = 4 + charCountBits + byteLen * 8;
    const capacityBits = v.totalDataCodewords * 8;
    if (dataBits <= capacityBits) return v;
  }
  return null;
}

/** ビット列に byte モードのデータを詰める。 */
function buildDataCodewords(bytes: number[], spec: VersionSpec): number[] {
  const bits: number[] = [];
  const push = (val: number, len: number) => {
    for (let i = len - 1; i >= 0; i--) bits.push((val >> i) & 1);
  };
  // モード指示子: byte = 0100
  push(0b0100, 4);
  const charCountBits = spec.version <= 9 ? 8 : 16;
  push(bytes.length, charCountBits);
  for (const b of bytes) push(b, 8);
  // 終端 0000（容量まで最大 4 ビット）
  const capacityBits = spec.totalDataCodewords * 8;
  const terminator = Math.min(4, capacityBits - bits.length);
  for (let i = 0; i < terminator; i++) bits.push(0);
  // バイト境界まで 0 パディング
  while (bits.length % 8 !== 0) bits.push(0);
  // コードワード化
  const codewords: number[] = [];
  for (let i = 0; i < bits.length; i += 8) {
    let byte = 0;
    for (let j = 0; j < 8; j++) byte = (byte << 1) | bits[i + j];
    codewords.push(byte);
  }
  // パッドバイト 0xEC / 0x11 を交互に
  const pads = [0xec, 0x11];
  let p = 0;
  while (codewords.length < spec.totalDataCodewords) {
    codewords.push(pads[p % 2]);
    p++;
  }
  return codewords;
}

/** データブロックと EC ブロックをインターリーブして最終コードワード列を作る。 */
function interleave(dataCodewords: number[], spec: VersionSpec): number[] {
  const dataBlocks: number[][] = [];
  const ecBlocks: number[][] = [];
  let offset = 0;
  for (const grp of spec.blocks) {
    for (let i = 0; i < grp.count; i++) {
      const block = dataCodewords.slice(offset, offset + grp.dataCodewords);
      offset += grp.dataCodewords;
      dataBlocks.push(block);
      ecBlocks.push(rsEncode(block, spec.ecPerBlock));
    }
  }
  const result: number[] = [];
  const maxData = Math.max(...dataBlocks.map((b) => b.length));
  for (let i = 0; i < maxData; i++) {
    for (const b of dataBlocks) if (i < b.length) result.push(b[i]);
  }
  const maxEc = Math.max(...ecBlocks.map((b) => b.length));
  for (let i = 0; i < maxEc; i++) {
    for (const b of ecBlocks) if (i < b.length) result.push(b[i]);
  }
  return result;
}

// ─── モジュール配置 ───

type Grid = { size: number; modules: (boolean | null)[][]; reserved: boolean[][] };

function newGrid(size: number): Grid {
  const modules: (boolean | null)[][] = [];
  const reserved: boolean[][] = [];
  for (let i = 0; i < size; i++) {
    modules.push(new Array<boolean | null>(size).fill(null));
    reserved.push(new Array<boolean>(size).fill(false));
  }
  return { size, modules, reserved };
}

function placeFinder(g: Grid, row: number, col: number) {
  for (let r = -1; r <= 7; r++) {
    for (let c = -1; c <= 7; c++) {
      const rr = row + r;
      const cc = col + c;
      if (rr < 0 || rr >= g.size || cc < 0 || cc >= g.size) continue;
      const isBorder =
        (r >= 0 && r <= 6 && (c === 0 || c === 6)) ||
        (c >= 0 && c <= 6 && (r === 0 || r === 6));
      const isCore = r >= 2 && r <= 4 && c >= 2 && c <= 4;
      g.modules[rr][cc] = isBorder || isCore;
      g.reserved[rr][cc] = true;
    }
  }
}

function placeAlignment(g: Grid, spec: VersionSpec) {
  const positions = spec.alignment;
  for (const r of positions) {
    for (const c of positions) {
      // ファインダと重なる位置はスキップ
      if ((r === 6 && c === 6) || (r === 6 && c === positions[positions.length - 1]) || (r === positions[positions.length - 1] && c === 6)) {
        continue;
      }
      if (g.reserved[r][c]) continue;
      for (let dr = -2; dr <= 2; dr++) {
        for (let dc = -2; dc <= 2; dc++) {
          const isDark = Math.max(Math.abs(dr), Math.abs(dc)) !== 1;
          g.modules[r + dr][c + dc] = isDark;
          g.reserved[r + dr][c + dc] = true;
        }
      }
    }
  }
}

function placeTiming(g: Grid) {
  for (let i = 8; i < g.size - 8; i++) {
    const val = i % 2 === 0;
    if (!g.reserved[6][i]) {
      g.modules[6][i] = val;
      g.reserved[6][i] = true;
    }
    if (!g.reserved[i][6]) {
      g.modules[i][6] = val;
      g.reserved[i][6] = true;
    }
  }
}

function reserveFormatAreas(g: Grid) {
  // フォーマット情報の予約領域 + ダークモジュール
  const size = g.size;
  for (let i = 0; i < 9; i++) {
    if (!g.reserved[8][i]) g.reserved[8][i] = true;
    if (!g.reserved[i][8]) g.reserved[i][8] = true;
  }
  for (let i = 0; i < 8; i++) {
    g.reserved[8][size - 1 - i] = true;
    g.reserved[size - 1 - i][8] = true;
  }
  // ダークモジュール
  g.modules[size - 8][8] = true;
  g.reserved[size - 8][8] = true;
}

function placeData(g: Grid, codewords: number[]) {
  const bits: number[] = [];
  for (const cw of codewords) for (let i = 7; i >= 0; i--) bits.push((cw >> i) & 1);
  let bitIdx = 0;
  const size = g.size;
  let upward = true;
  for (let col = size - 1; col > 0; col -= 2) {
    if (col === 6) col--; // タイミングパターン列をスキップ
    for (let i = 0; i < size; i++) {
      const row = upward ? size - 1 - i : i;
      for (let c = 0; c < 2; c++) {
        const cc = col - c;
        if (g.reserved[row][cc]) continue;
        const bit = bitIdx < bits.length ? bits[bitIdx] : 0;
        bitIdx++;
        g.modules[row][cc] = bit === 1;
      }
    }
    upward = !upward;
  }
}

// マスクパターン 0: (row + col) % 2 === 0
function applyMask0(g: Grid) {
  for (let r = 0; r < g.size; r++) {
    for (let c = 0; c < g.size; c++) {
      if (g.reserved[r][c]) continue;
      if ((r + c) % 2 === 0) g.modules[r][c] = !g.modules[r][c];
    }
  }
}

// フォーマット情報（EC レベル L=01, マスク 0=000）を配置
function placeFormatInfo(g: Grid) {
  // L + mask0 の 15bit フォーマット情報（BCH 計算済みの既知値）
  // data bits: 01 000 -> 0b01000 ; 標準の固定値を使用
  const formatBits = 0b111011111000100; // L, mask 0
  const size = g.size;
  const bitAt = (i: number) => (formatBits >> i) & 1;
  // 左上・縦横 + 右上/左下ミラー
  // 横方向（上端）
  const setModule = (r: number, c: number, v: number) => {
    g.modules[r][c] = v === 1;
    g.reserved[r][c] = true;
  };
  // 標準の配置（15bit を規定位置へ）
  // 参考実装に沿った配置
  const coords1: [number, number][] = [
    [8, 0], [8, 1], [8, 2], [8, 3], [8, 4], [8, 5], [8, 7], [8, 8],
    [7, 8], [5, 8], [4, 8], [3, 8], [2, 8], [1, 8], [0, 8],
  ];
  coords1.forEach(([r, c], idx) => setModule(r, c, bitAt(idx)));
  const coords2: [number, number][] = [
    [size - 1, 8], [size - 2, 8], [size - 3, 8], [size - 4, 8],
    [size - 5, 8], [size - 6, 8], [size - 7, 8],
    [8, size - 8], [8, size - 7], [8, size - 6], [8, size - 5],
    [8, size - 4], [8, size - 3], [8, size - 2], [8, size - 1],
  ];
  coords2.forEach(([r, c], idx) => setModule(r, c, bitAt(idx)));
}

/**
 * テキストを QR モジュール行列に変換する。
 * 失敗時（容量超過など）は null を返す。
 */
export function generateQrMatrix(text: string): boolean[][] | null {
  // UTF-8 エンコード
  const bytes = Array.from(new TextEncoder().encode(text));
  const spec = pickVersion(bytes.length);
  if (!spec) return null;

  const dataCodewords = buildDataCodewords(bytes, spec);
  const finalCodewords = interleave(dataCodewords, spec);

  const g = newGrid(spec.size);
  placeFinder(g, 0, 0);
  placeFinder(g, 0, spec.size - 7);
  placeFinder(g, spec.size - 7, 0);
  placeAlignment(g, spec);
  placeTiming(g);
  reserveFormatAreas(g);
  placeData(g, finalCodewords);
  applyMask0(g);
  placeFormatInfo(g);

  return g.modules.map((row) => row.map((m) => m === true));
}
