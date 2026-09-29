"""极简 QR 码编码器（纯标准库，byte 模式，纠错等级 Q）
用途：NapCat 只在启动时落盘 cache/qrcode.png，之后刷新二维码不再更新文件，
面板因此永远显示过期码。改为直接用 WebUI API 拿到实时 URL 后本地出图。

仅覆盖 version 1-10（NapCat 登录 URL 约 68 字节，落在 version 6-Q）。
输出 1-bit 灰度 PNG（无依赖，zlib 手写）。
"""
import struct
import zlib

# (version, level) -> [ec_per_block, (blocks, data_per_block), ...]
_BLOCKS = {
    (1, "L"): (7, [(1, 19)]), (1, "M"): (10, [(1, 16)]), (1, "Q"): (13, [(1, 13)]), (1, "H"): (17, [(1, 9)]),
    (2, "L"): (10, [(1, 34)]), (2, "M"): (16, [(1, 28)]), (2, "Q"): (22, [(1, 22)]), (2, "H"): (28, [(1, 16)]),
    (3, "L"): (15, [(1, 55)]), (3, "M"): (26, [(1, 44)]), (3, "Q"): (18, [(2, 17)]), (3, "H"): (22, [(2, 13)]),
    (4, "L"): (20, [(1, 80)]), (4, "M"): (18, [(2, 32)]), (4, "Q"): (26, [(2, 24)]), (4, "H"): (16, [(4, 9)]),
    (5, "L"): (26, [(1, 108)]), (5, "M"): (24, [(2, 43)]), (5, "Q"): (18, [(2, 15), (2, 16)]), (5, "H"): (22, [(2, 11), (2, 12)]),
    (6, "L"): (18, [(2, 68)]), (6, "M"): (16, [(4, 27)]), (6, "Q"): (24, [(4, 19)]), (6, "H"): (28, [(4, 15)]),
    (7, "L"): (20, [(2, 78)]), (7, "M"): (18, [(4, 31)]), (7, "Q"): (18, [(2, 14), (4, 15)]), (7, "H"): (26, [(4, 13), (1, 14)]),
    (8, "L"): (24, [(2, 97)]), (8, "M"): (22, [(2, 38), (2, 39)]), (8, "Q"): (22, [(4, 18), (2, 19)]), (8, "H"): (26, [(4, 14), (2, 15)]),
    (9, "L"): (30, [(2, 116)]), (9, "M"): (22, [(3, 36), (2, 37)]), (9, "Q"): (20, [(4, 16), (4, 17)]), (9, "H"): (24, [(4, 12), (4, 13)]),
    (10, "L"): (18, [(2, 68), (2, 69)]), (10, "M"): (26, [(4, 43), (1, 44)]), (10, "Q"): (24, [(6, 19), (2, 20)]), (10, "H"): (28, [(6, 15), (2, 16)]),
}

_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}

_LEVEL_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}

# ---- GF(256) ----
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _gen_poly(n):
    g = [1]
    for i in range(n):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= _mul(c, 1)
            ng[j + 1] ^= _mul(c, _EXP[i])
        g = ng
    return g


def _rs_ecc(data, n):
    g = _gen_poly(n)
    res = list(data) + [0] * n
    for i in range(len(data)):
        coef = res[i]
        if coef:
            for j in range(1, len(g)):
                res[i + j] ^= _mul(g[j], coef)
    return res[len(data):]


class _Bits:
    def __init__(self):
        self.b = []

    def put(self, val, n):
        for i in range(n - 1, -1, -1):
            self.b.append((val >> i) & 1)


def _capacity(v, level):
    _, groups = _BLOCKS[(v, level)]
    return sum(cnt * dc for cnt, dc in groups)


def _best_version(nbytes, level):
    need = 4 + (8 if True else 16) + nbytes * 8  # 先按 1-9 的 8 位计数
    for v in range(1, 11):
        cbits = 8 if v <= 9 else 16
        need = 4 + cbits + nbytes * 8
        if _capacity(v, level) * 8 >= need:
            return v
    raise ValueError("内容过长，超出 version 1-10 支持范围")


def _build_codewords(data: bytes, v: int, level: str):
    bits = _Bits()
    bits.put(0b0100, 4)
    bits.put(len(data), 8 if v <= 9 else 16)
    for byte in data:
        bits.put(byte, 8)
    cap = _capacity(v, level) * 8
    for _ in range(min(4, cap - len(bits.b))):
        bits.b.append(0)
    while len(bits.b) % 8:
        bits.b.append(0)
    codewords = [int("".join(map(str, bits.b[i:i + 8])), 2) for i in range(0, len(bits.b), 8)]
    pad = [0xEC, 0x11]
    i = 0
    while len(codewords) < _capacity(v, level):
        codewords.append(pad[i % 2])
        i += 1

    ec_len, groups = _BLOCKS[(v, level)]
    blocks, ecblocks, pos = [], [], 0
    for cnt, dc in groups:
        for _ in range(cnt):
            blk = codewords[pos:pos + dc]
            pos += dc
            blocks.append(blk)
            ecblocks.append(_rs_ecc(blk, ec_len))

    out = []
    maxd = max(len(b) for b in blocks)
    for i in range(maxd):
        for blk in blocks:
            if i < len(blk):
                out.append(blk[i])
    for i in range(ec_len):
        for blk in ecblocks:
            out.append(blk[i])
    return out


class _Matrix:
    def __init__(self, v):
        self.v = v
        self.n = 17 + 4 * v
        self.m = [[None] * self.n for _ in range(self.n)]  # None=空，True/False=功能模块

    def _set(self, r, c, val):
        if 0 <= r < self.n and 0 <= c < self.n:
            self.m[r][c] = val

    def finder(self, r0, c0):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if not (0 <= rr < self.n and 0 <= cc < self.n):
                    continue
                inb = 0 <= r <= 6 and 0 <= c <= 6
                dark = inb and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4))
                self._set(rr, cc, bool(dark))

    def alignment(self, r0, c0):
        for r in range(-2, 3):
            for c in range(-2, 3):
                dark = max(abs(r), abs(c)) != 1
                self._set(r0 + r, c0 + c, dark)

    def build_function(self):
        self.finder(0, 0)
        self.finder(0, self.n - 7)
        self.finder(self.n - 7, 0)
        for i in range(8, self.n - 8):
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for r in _ALIGN[self.v]:
            for c in _ALIGN[self.v]:
                if (r < 9 and c < 9) or (r < 9 and c > self.n - 10) or (r > self.n - 10 and c < 9):
                    continue
                self.alignment(r, c)
        self._set(self.n - 8, 8, True)  # 暗模块
        # 预留格式信息位
        for i in range(9):
            if self.m[8][i] is None:
                self._set(8, i, False)
            if self.m[i][8] is None:
                self._set(i, 8, False)
        for i in range(8):
            self._set(8, self.n - 1 - i, False)
            self._set(self.n - 1 - i, 8, False)
        if self.v >= 7:
            for r in range(6):
                for c in range(3):
                    self._set(self.n - 11 + c, r, False)
                    self._set(r, self.n - 11 + c, False)

    def place(self, codewords, mask):
        bits = []
        for cw in codewords:
            for i in range(7, -1, -1):
                bits.append((cw >> i) & 1)
        idx = 0
        col = self.n - 1
        upward = True
        while col > 0:
            if col == 6:
                col -= 1
            rng = range(self.n - 1, -1, -1) if upward else range(self.n)
            for r in rng:
                for c in (col, col - 1):
                    if self.m[r][c] is None:
                        bit = bits[idx] if idx < len(bits) else 0
                        idx += 1
                        if _mask(mask, r, c):
                            bit ^= 1
                        self.m[r][c] = bool(bit)
            upward = not upward
            col -= 2

    def write_format(self, level, mask):
        data = (_LEVEL_BITS[level] << 3) | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        fmt = ((data << 10) | rem) ^ 0x5412
        bits = [(fmt >> i) & 1 for i in range(14, -1, -1)]
        coords = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
                  (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
        for (r, c), b in zip(coords, bits):
            self.m[r][c] = bool(b)
        for i in range(8):
            self.m[self.n - 1 - i][8] = bool(bits[i])
        for i in range(8, 15):
            self.m[8][self.n - 15 + i] = bool(bits[i])
        self.m[self.n - 8][8] = True


def _mask(mask, r, c):
    if mask == 0:
        return (r + c) % 2 == 0
    if mask == 1:
        return r % 2 == 0
    if mask == 2:
        return c % 3 == 0
    if mask == 3:
        return (r + c) % 3 == 0
    if mask == 4:
        return (r // 2 + c // 3) % 2 == 0
    if mask == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if mask == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _penalty(m):
    n = len(m)
    score = 0
    for line in list(m) + [list(col) for col in zip(*m)]:
        run, prev = 1, line[0]
        for v in line[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, v
        if run >= 5:
            score += 3 + (run - 5)
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    pat1 = [True, False, True, True, True, False, True, False, False, False, False]
    pat2 = [False, False, False, False, True, False, True, True, True, False, True]
    for line in list(m) + [list(col) for col in zip(*m)]:
        for i in range(n - 10):
            if line[i:i + 11] == pat1 or line[i:i + 11] == pat2:
                score += 40
    dark = sum(1 for row in m for v in row if v)
    ratio = dark * 100 // (n * n)
    score += 10 * (abs(ratio - 50) // 5)
    return score


def make_matrix(text: str, level: str = "Q", force_mask: int | None = None):
    data = text.encode("utf-8")
    v = _best_version(len(data), level)
    cw = _build_codewords(data, v, level)
    best, best_score = None, None
    for mask in ([force_mask] if force_mask is not None else range(8)):
        mx = _Matrix(v)
        mx.build_function()
        mx.place(cw, mask)
        mx.write_format(level, mask)
        if force_mask is not None:
            return mx.m
        s = _penalty(mx.m)
        if best_score is None or s < best_score:
            best, best_score = mx.m, s
    return best


def _png(matrix, scale=4, quiet=4) -> bytes:
    n = len(matrix)
    total = (n + 2 * quiet) * scale
    raw = bytearray()
    for y in range(total):
        my = y // scale - quiet
        row = bytearray()
        for x in range(total):
            mx = x // scale - quiet
            dark = 0 <= my < n and 0 <= mx < n and matrix[my][mx]
            row.append(0x00 if dark else 0xFF)
        raw.append(0)  # filter: none
        raw += row

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data +
                struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", total, total, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def data_url(text: str, level: str = "Q", scale: int = 4) -> str:
    import base64
    return "data:image/png;base64," + base64.b64encode(
        _png(make_matrix(text, level), scale=scale)).decode()
