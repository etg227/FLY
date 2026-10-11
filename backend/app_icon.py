"""纯 Python 生成 FLY 图标（蓝紫渐变圆角方块 + 白色 F），不依赖 PIL。

同一套像素既写成 .ico（窗口/任务栏/托盘用），也能交给 Tk 的 PhotoImage。
active=True 时右下角带绿色圆点，表示正在加速。
"""
from __future__ import annotations
import struct
from pathlib import Path

ACCENT = (0x3D, 0x7B, 0xFF)
ACCENT2 = (0x8E, 0x5C, 0xFF)
GREEN = (0x22, 0xB5, 0x73)
SIZES = (16, 20, 24, 32, 48, 64)
_SS = 4  # 每像素 4x4 超采样做抗锯齿


def _inside_rounded(x, y, n, r):
    cx = min(max(x, r), n - r)
    cy = min(max(y, r), n - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _inside_f(x, y, n):
    u, v = x * 32.0 / n, y * 32.0 / n
    stem = 10.0 <= u <= 14.6 and 7.0 <= v <= 25.0
    top = 10.0 <= u <= 23.0 and 7.0 <= v <= 11.4
    mid = 10.0 <= u <= 20.6 and 14.4 <= v <= 18.6
    return stem or top or mid


def render(n, active=False):
    """返回 n*n 个 (r, g, b, a)，按行从上到下。"""
    r = n * 0.23
    dot_c, dot_r, ring = n * 0.78, n * 0.2, n * 0.06
    out = []
    for py in range(n):
        for px in range(n):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(_SS):
                for sx in range(_SS):
                    x = px + (sx + 0.5) / _SS
                    y = py + (sy + 0.5) / _SS
                    color = None
                    if active and (x - dot_c) ** 2 + (y - dot_c) ** 2 <= dot_r ** 2:
                        inner = (x - dot_c) ** 2 + (y - dot_c) ** 2 <= (dot_r - ring) ** 2
                        color = GREEN if inner else (255, 255, 255)
                    elif _inside_rounded(x, y, n, r):
                        if _inside_f(x, y, n):
                            color = (255, 255, 255)
                        else:
                            t = min(1.0, max(0.0, (x + y) / (2 * n)))
                            color = tuple(round(a + (b - a) * t) for a, b in zip(ACCENT, ACCENT2))
                    if color:
                        acc[0] += color[0]; acc[1] += color[1]; acc[2] += color[2]; acc[3] += 1
            k = acc[3]
            if k:
                out.append((round(acc[0] / k), round(acc[1] / k), round(acc[2] / k),
                            round(255 * k / (_SS * _SS))))
            else:
                out.append((0, 0, 0, 0))
    return out


def ico_bytes(active=False, sizes=SIZES):
    images = []
    for n in sizes:
        px = render(n, active)
        rows = []
        for y in range(n - 1, -1, -1):  # BMP 自下而上
            rows.append(b"".join(struct.pack("BBBB", b, g, r, a) for r, g, b, a in px[y * n:(y + 1) * n]))
        xor = b"".join(rows)
        mask_row = ((n + 31) // 32) * 4
        and_mask = b"\x00" * (mask_row * n)
        header = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, len(xor) + len(and_mask), 0, 0, 0, 0)
        images.append(header + xor + and_mask)
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for n, data in zip(sizes, images):
        out += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images)


ICON_VERSION = 1


def write_icons(directory: Path):
    """生成 fly-v1.ico / fly-active-v1.ico；返回 (idle, active) 路径。

    渲染一次约要 0.8 秒，所以文件名带版本号、存在就直接复用，
    只有首次运行或图标设计改版（ICON_VERSION 变化）时才重新生成。
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for stem, active in (("fly", False), ("fly-active", True)):
        p = directory / f"{stem}-v{ICON_VERSION}.ico"
        try:
            if not p.exists() or p.stat().st_size < 64:
                tmp = p.with_suffix(".tmp")
                tmp.write_bytes(ico_bytes(active))
                tmp.replace(p)
        except OSError:
            pass
        paths.append(p)
    return tuple(paths)


def photo_rows(n=32, active=False):
    """Tk PhotoImage.put() 需要的 '{#rrggbb ...}' 行；透明像素用窗口背景色近似。"""
    px = render(n, active)
    rows = []
    for y in range(n):
        rows.append("{" + " ".join("#%02x%02x%02x" % p[:3] if p[3] > 127 else "#f2f4f9"
                                    for p in px[y * n:(y + 1) * n]) + "}")
    return " ".join(rows)
