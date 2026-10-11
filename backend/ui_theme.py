"""FLY 界面主题与自绘控件（参考 BakaXL 4.0 的布局：左侧导航栏 + 横幅 + 圆角卡片 + 大启动按钮）。

只依赖 tkinter，不引入第三方包，保证离线启动包照常可用。
"""
from __future__ import annotations
import math
import tkinter as tk
from tkinter import ttk, font as tkfont

# ---- 配色 ---------------------------------------------------------------
C = {
    "bg":        "#F2F4F9",   # 内容区背景
    "card":      "#FFFFFF",
    "card_alt":  "#F7F8FC",
    "border":    "#E2E6EF",
    "text":      "#1D2333",
    "subtext":   "#6B7489",
    "muted":     "#9AA2B4",
    "accent":    "#3D7BFF",
    "accent_hi": "#5A8FFF",
    "accent_lo": "#2F67E0",
    "accent_bg": "#EAF1FF",
    "accent2":   "#8E5CFF",   # 横幅渐变终点
    "nav":       "#161A27",
    "nav_hi":    "#232838",
    "nav_sel":   "#2A3044",
    "nav_text":  "#A9B1C6",
    "nav_text_sel": "#FFFFFF",
    "ok":        "#22B573",
    "warn":      "#F0A020",
    "err":       "#E5484D",
    "idle":      "#8A93A6",
    "log_bg":    "#141824",
    "log_fg":    "#C9D1E3",
}

FONTS = {}
ICONS = {}

_ICON_FLUENT = {"home": "", "profiles": "", "log": "",
                "settings": "", "about": "", "check": "",
                "bolt": "", "stop": "", "swap": "",
                "refresh": "", "add": "", "globe": "",
                "chip": "", "link": "", "speed": ""}
_ICON_PLAIN = {"home": "⌂", "profiles": "☰", "log": "≡", "settings": "⚙",
               "about": "ⓘ", "check": "✓", "bolt": "⚡", "stop": "■",
               "swap": "⇄", "refresh": "↻", "add": "+", "globe": "◎",
               "chip": "▣", "link": "⛓", "speed": "↯"}


def _pick(families, wanted, fallback):
    for name in wanted:
        if name in families:
            return name
    return fallback


def apply_theme(root):
    """初始化字体、图标字体和 ttk 样式。必须在创建任何窗口控件前调用。"""
    families = set(tkfont.families(root))
    ui = _pick(families, ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI",
                          "Noto Sans CJK SC", "PingFang SC", "Source Han Sans SC"], "TkDefaultFont")
    mono = _pick(families, ["Cascadia Mono", "Consolas", "JetBrains Mono",
                            "DejaVu Sans Mono", "Noto Sans Mono"], "TkFixedFont")
    icon = _pick(families, ["Segoe Fluent Icons", "Segoe MDL2 Assets"], None)
    FONTS.update({
        "ui": (ui, 10), "ui_b": (ui, 10, "bold"), "small": (ui, 9),
        "h1": (ui, 26, "bold"), "h2": (ui, 16, "bold"), "h3": (ui, 12, "bold"),
        "value": (ui, 13, "bold"), "big": (ui, 15, "bold"), "mono": (mono, 9),
        "icon": (icon, 13) if icon else (ui, 13), "icon_s": (icon, 11) if icon else (ui, 11),
    })
    ICONS.clear(); ICONS.update(_ICON_FLUENT if icon else _ICON_PLAIN)

    root.configure(bg=C["bg"])
    root.option_add("*Font", FONTS["ui"])
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=C["bg"], foreground=C["text"], font=FONTS["ui"],
                    bordercolor=C["border"], lightcolor=C["border"], darkcolor=C["border"],
                    troughcolor=C["bg"], focuscolor=C["accent"])
    style.configure("TFrame", background=C["bg"])
    style.configure("TLabel", background=C["bg"], foreground=C["text"])
    style.configure("TLabelframe", background=C["bg"], bordercolor=C["border"], relief="solid")
    style.configure("TLabelframe.Label", background=C["bg"], foreground=C["subtext"], font=FONTS["ui_b"])
    style.configure("TButton", background=C["card"], foreground=C["text"], padding=(12, 5),
                    bordercolor=C["border"], relief="flat")
    style.map("TButton", background=[("disabled", C["card_alt"]), ("pressed", C["accent_bg"]),
                                     ("active", C["accent_bg"])],
              foreground=[("disabled", C["muted"])],
              bordercolor=[("active", C["accent"]), ("focus", C["accent"])])
    style.configure("Accent.TButton", background=C["accent"], foreground="#FFFFFF",
                    bordercolor=C["accent"])
    style.map("Accent.TButton", background=[("pressed", C["accent_lo"]), ("active", C["accent_hi"])],
              foreground=[("active", "#FFFFFF")])
    style.configure("TEntry", fieldbackground=C["card"], bordercolor=C["border"], padding=4)
    style.map("TEntry", bordercolor=[("focus", C["accent"])], lightcolor=[("focus", C["accent"])])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=C["bg"], foreground=C["text"],
                        indicatorbackground=C["card"], indicatorforeground=C["accent"])
        style.map(name, background=[("active", C["bg"])],
                  indicatorbackground=[("selected", C["card"])])
    style.configure("Vertical.TScrollbar", background=C["border"], troughcolor=C["bg"],
                    bordercolor=C["bg"], arrowcolor=C["subtext"], gripcount=0, relief="flat")
    style.configure("Log.Vertical.TScrollbar", background="#2A3044", troughcolor=C["log_bg"],
                    bordercolor=C["log_bg"], arrowcolor=C["nav_text"], gripcount=0, relief="flat")
    style.map("Log.Vertical.TScrollbar", background=[("active", "#3A425A")])


def style_toplevel(win):
    win.configure(bg=C["bg"])


# ---- 绘图工具 -----------------------------------------------------------

def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, splinesteps=12, **kw)


def _mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


# ---- 圆角卡片 -----------------------------------------------------------

class Card(tk.Frame):
    """白色圆角卡片。内容放进 card.body（普通 tk.Frame），尺寸由内容决定。

    圆角背景画在垫底的 Canvas 上，body 四周留出 pad 边距，圆角落在边距里。
    """

    def __init__(self, master, pad=16, radius=12, fill=None, outline=None, **kw):
        parent_bg = kw.pop("bg", None) or _bg_of(master)
        super().__init__(master, bg=parent_bg, **kw)
        self.radius = radius
        self.fill = fill or C["card"]
        self.outline = outline or C["border"]
        self._canvas = tk.Canvas(self, bg=parent_bg, highlightthickness=0, bd=0)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=self.fill)
        self.body.pack(fill="both", expand=True, padx=pad, pady=pad)
        self._canvas.bind("<Configure>", self._redraw)

    def set_colors(self, fill=None, outline=None):
        if fill:
            self.fill = fill
            _recolor(self.body, fill)
        if outline:
            self.outline = outline
        self._redraw()

    def _redraw(self, _e=None):
        cv = self._canvas
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.delete("all")
        if w > 2 and h > 2:
            rounded_rect(cv, 1, 1, w - 1, h - 1, self.radius, fill=self.fill, outline=self.outline, width=1)


def _bg_of(widget):
    try:
        return widget.cget("bg")
    except tk.TclError:
        return C["bg"]


def _recolor(widget, color):
    """把 body 内保持和卡片同色的子控件一起换色（选中态高亮用）。"""
    try:
        widget.configure(bg=color)
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        if getattr(child, "_follow_card", True) and not isinstance(child, tk.Canvas):
            _recolor(child, color)


# ---- 圆角按钮 -----------------------------------------------------------

class RoundButton(tk.Canvas):
    """自绘圆角按钮，兼容 .configure(state=..., text=...)，可选副标题（BakaXL 式大启动按钮）。"""

    KINDS = {
        "accent":    (C["accent"], C["accent_hi"], C["accent_lo"], "#FFFFFF", "#DCE3F5"),
        "secondary": (C["card"], C["accent_bg"], "#DCE7FF", C["text"], C["card_alt"]),
        "danger":    (C["card"], "#FDECEC", "#FADADB", C["err"], C["card_alt"]),
        "ghost":     (C["nav"], C["nav_hi"], C["nav_sel"], C["nav_text"], C["nav"]),
    }

    def __init__(self, master, text="", command=None, kind="secondary", icon=None,
                 subtitle=None, width=120, height=38, radius=10, font=None, **kw):
        bg = kw.pop("bg", None) or _bg_of(master)
        super().__init__(master, bg=bg, width=width, height=height,
                         highlightthickness=0, bd=0, cursor="hand2", **kw)
        self._text, self._icon, self._subtitle = text, icon, subtitle
        self._command = command
        self._kind = kind
        self._radius = radius
        self._font = font or FONTS["ui_b"]
        self._state = "normal"
        self._hover = self._press = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, press=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(press=True))
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def _set(self, hover=None, press=None):
        if hover is not None: self._hover = hover
        if press is not None: self._press = press
        self._draw()

    def _release(self, e):
        fire = self._press and self._hover and self._state != "disabled"
        self._set(press=False)
        if fire and self._command:
            self._command()

    def _draw(self):
        base, hover, press, fg, disabled = self.KINDS[self._kind]
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            w, h = int(self.cget("width")), int(self.cget("height"))
        off = self._state == "disabled"
        fill = disabled if off else press if self._press else hover if self._hover else base
        color = C["muted"] if off else fg
        self.delete("all")
        outline = "" if self._kind in ("accent", "ghost") else C["border"]
        if self._kind == "accent" and not off:
            rounded_rect(self, 2, 4, w - 2, h, self._radius, fill=_mix(C["accent"], C["bg"], .55), outline="")
            rounded_rect(self, 1, 1, w - 1, h - 3, self._radius, fill=fill, outline="")
        else:
            rounded_rect(self, 1, 1, w - 1, h - 1, self._radius, fill=fill, outline=outline)
        cy = (h - 3) / 2 if self._kind == "accent" else h / 2
        label = self._text
        if self._subtitle:
            self.create_text(w / 2, cy - 9, text=label, fill=color, font=self._font)
            self.create_text(w / 2, cy + 13, text=self._subtitle, fill=color if off else _mix(color, fill, .25),
                             font=FONTS["small"], width=w - 20)
        elif self._icon:
            ft = tkfont.Font(font=self._font)
            iw = 22
            total = iw + ft.measure(label)
            x0 = (w - total) / 2
            self.create_text(x0 + iw / 2 - 2, cy, text=ICONS.get(self._icon, ""), fill=color, font=FONTS["icon_s"])
            self.create_text(x0 + iw, cy, text=label, fill=color, font=self._font, anchor="w")
        else:
            self.create_text(w / 2, cy, text=label, fill=color, font=self._font)

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        changed = False
        for key, attr in (("state", "_state"), ("text", "_text"),
                          ("subtitle", "_subtitle"), ("command", "_command")):
            if key in kw:
                setattr(self, attr, kw.pop(key)); changed = True
        result = super().configure(**kw) if kw else None
        if changed:
            self.configure_cursor()
            self._draw()
        return result
    config = configure

    def configure_cursor(self):
        super().configure(cursor="arrow" if self._state == "disabled" else "hand2")

    def cget(self, key):
        if key == "state": return self._state
        if key == "text": return self._text
        return super().cget(key)


# ---- 状态胶囊 -----------------------------------------------------------

def status_color(text):
    t = text or ""
    if "加速中" in t:
        return C["ok"]
    if any(k in t for k in ("异常", "不可用", "失败", "需要修复")):
        return C["err"]
    if any(k in t for k in ("启动", "检测", "中...", "等待", "校验", "暂时")):
        return C["warn"]
    return C["idle"]


class StatusPill(tk.Canvas):
    """绑定 StringVar 的圆角状态胶囊，颜色随状态文字变化。"""

    def __init__(self, master, variable, bg=None, on_dark=False, **kw):
        super().__init__(master, bg=bg or _bg_of(master), highlightthickness=0, bd=0, height=30, **kw)
        self.var, self.on_dark = variable, on_dark
        self._trace = variable.trace_add("write", lambda *_: self._draw())
        self.bind("<Destroy>", self._untrace)
        self._draw()

    def _untrace(self, _e=None):
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    def _draw(self):
        try:
            text = self.var.get()
        except tk.TclError:
            return
        color = status_color(text)
        ft = tkfont.Font(font=FONTS["ui_b"])
        w = ft.measure(text) + 40
        self.configure(width=w)
        self.delete("all")
        fill = "#FFFFFF" if self.on_dark else _mix(color, "#FFFFFF", .86)
        rounded_rect(self, 1, 1, w - 1, 29, 14, fill=fill, outline="")
        self.create_oval(13, 11, 21, 19, fill=color, outline="")
        self.create_text(28, 15, text=text, anchor="w", font=FONTS["ui_b"],
                         fill=C["text"] if self.on_dark else _mix(color, "#000000", .25))


# ---- 横幅 ---------------------------------------------------------------

class Hero(tk.Canvas):
    """渐变横幅（BakaXL 首页的大背景区）。右上角可放状态胶囊。"""

    def __init__(self, master, title, subtitle, badge="", height=170, **kw):
        super().__init__(master, bg=_bg_of(master), highlightthickness=0, bd=0, height=height, **kw)
        self.title, self.subtitle, self.badge = title, subtitle, badge
        self.bind("<Configure>", lambda e: self._draw())
        self._overlay = []

    def place_widget(self, widget, relx_right=24, y=22):
        self._overlay.append((widget, relx_right, y))
        self._draw()

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        self.delete("all")
        steps = max(2, w // 3)
        for i in range(steps):
            x0 = i * w / steps
            x1 = (i + 1) * w / steps + 1
            self.create_rectangle(x0, 0, x1, h, outline="",
                                  fill=_mix(C["accent"], C["accent2"], i / (steps - 1)))
        # 装饰圆环
        for cx, cy, r, t in ((w - 90, -10, 130, .82), (w - 230, h + 30, 90, .86)):
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=_mix(C["accent2"], "#FFFFFF", t - .4),
                             width=2)
            self.create_oval(cx - r + 26, cy - r + 26, cx + r - 26, cy + r - 26, outline="",
                             fill=_mix(_mix(C["accent"], C["accent2"], min(1, cx / w)), "#FFFFFF", .12))
        # 圆角遮罩
        bg, r = self.cget("bg"), 14
        for (cx, cy, sx, sy) in ((0, 0, 1, 1), (w, 0, -1, 1), (0, h, 1, -1), (w, h, -1, -1)):
            pts = [cx, cy]
            for k in range(0, 91, 10):
                a = math.radians(k)
                pts += [cx + sx * (r - r * math.cos(a)), cy + sy * (r - r * math.sin(a))]
            self.create_polygon(pts, fill=bg, outline=bg)
        self.create_text(32, 52, text=self.title, anchor="w", fill="#FFFFFF", font=FONTS["h1"])
        if self.badge:
            ft = tkfont.Font(font=FONTS["small"])
            tw = tkfont.Font(font=FONTS["h1"]).measure(self.title)
            bx = 32 + tw + 14
            bw = ft.measure(self.badge) + 18
            rounded_rect(self, bx, 40, bx + bw, 64, 11, fill=_mix(C["accent"], "#FFFFFF", .25), outline="")
            self.create_text(bx + bw / 2, 52, text=self.badge, fill="#FFFFFF", font=FONTS["small"])
        self.create_text(32, 98, text=self.subtitle, anchor="nw", fill=_mix("#FFFFFF", C["accent"], .12),
                         font=FONTS["ui"], width=max(200, w - 64))
        for widget, right, y in self._overlay:
            self.create_window(w - right, y, window=widget, anchor="ne")


# ---- 侧边导航 -----------------------------------------------------------

class NavItem(tk.Frame):
    def __init__(self, master, icon, text, command):
        super().__init__(master, bg=C["nav"], cursor="hand2")
        self.command, self.selected = command, False
        self.bar = tk.Frame(self, bg=C["nav"], width=3, height=22)
        self.bar.pack(side="left", padx=(8, 0))
        self.ic = tk.Label(self, text=ICONS.get(icon, ""), font=FONTS["icon"], bg=C["nav"],
                           fg=C["nav_text"], width=2)
        self.ic.pack(side="left", padx=(10, 6), pady=10)
        self.lb = tk.Label(self, text=text, font=FONTS["ui"], bg=C["nav"], fg=C["nav_text"], anchor="w")
        self.lb.pack(side="left", fill="x", expand=True)
        for w in (self, self.ic, self.lb, self.bar):
            w.bind("<Enter>", lambda e: self._paint(True))
            w.bind("<Leave>", lambda e: self._paint(False))
            w.bind("<Button-1>", lambda e: self.command())

    def set_selected(self, value):
        self.selected = value
        self._paint(False)

    def _paint(self, hover):
        bg = C["nav_sel"] if self.selected else C["nav_hi"] if hover else C["nav"]
        fg = C["nav_text_sel"] if self.selected or hover else C["nav_text"]
        for w in (self, self.ic, self.lb):
            w.configure(bg=bg)
        self.ic.configure(fg=C["accent_hi"] if self.selected else fg)
        self.lb.configure(fg=fg, font=FONTS["ui_b"] if self.selected else FONTS["ui"])
        self.bar.configure(bg=C["accent"] if self.selected else bg)


# ---- 开关 ---------------------------------------------------------------

class SwitchRow(tk.Frame):
    """开关 + 文字，替代原生复选框；兼容 .configure(text=...)。"""

    def __init__(self, master, variable, text="", command=None, **kw):
        bg = kw.pop("bg", None) or _bg_of(master)
        super().__init__(master, bg=bg, cursor="hand2", **kw)
        self.var, self.command = variable, command
        self.sw = tk.Canvas(self, width=40, height=22, bg=bg, highlightthickness=0, bd=0)
        self.sw.pack(side="left", padx=(0, 12))
        self.lb = tk.Label(self, text=text, bg=bg, fg=C["text"], font=FONTS["ui"], anchor="w",
                           justify="left", wraplength=640)
        self.lb.pack(side="left", fill="x", expand=True)
        for w in (self, self.sw, self.lb):
            w.bind("<Button-1>", self._toggle)
        self._trace = variable.trace_add("write", lambda *_: self._draw())
        self.bind("<Destroy>", self._untrace, add="+")
        self._draw()

    def _untrace(self, e):
        if e.widget is self:
            try: self.var.trace_remove("write", self._trace)
            except (tk.TclError, ValueError): pass

    def _toggle(self, _e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _draw(self):
        try:
            on = bool(self.var.get())
        except tk.TclError:
            return
        cv = self.sw
        cv.delete("all")
        rounded_rect(cv, 1, 1, 39, 21, 10, fill=C["accent"] if on else C["border"], outline="")
        x = 29 if on else 11
        cv.create_oval(x - 7, 4, x + 7, 18, fill="#FFFFFF", outline="")

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if "text" in kw:
            self.lb.configure(text=kw.pop("text"))
        return super().configure(**kw) if kw else None
    config = configure


def autohide(scrollbar, **pack):
    """返回一个 yscrollcommand：内容放得下时隐藏滚动条。"""
    def setter(first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            scrollbar.pack_forget()
        elif not scrollbar.winfo_ismapped():
            scrollbar.pack(**pack)
        scrollbar.set(first, last)
    return setter


# ---- 分流配置磁贴 -------------------------------------------------------

class ProfileTile(Card):
    """可勾选的配置卡片：点击任意位置切换 BooleanVar。"""

    def __init__(self, master, name, tags, variable):
        super().__init__(master, pad=12, radius=10)
        self.var = variable
        b = self.body
        self.box = tk.Canvas(b, width=22, height=22, highlightthickness=0, bd=0, bg=self.fill)
        self.box.pack(side="left", padx=(0, 10))
        text = tk.Frame(b, bg=self.fill); text.pack(side="left", fill="x", expand=True)
        self.name = tk.Label(text, text=name, font=FONTS["ui_b"], bg=self.fill, fg=C["text"], anchor="w")
        self.name.pack(fill="x")
        chips = tk.Frame(text, bg=self.fill); chips.pack(fill="x", pady=(3, 0))
        for tag in tags:
            chip = tk.Label(chips, text=tag, font=FONTS["small"], bg=C["accent_bg"], fg=C["accent_lo"], padx=6)
            chip._follow_card = False
            chip.pack(side="left", padx=(0, 6))
        self._trace = variable.trace_add("write", lambda *_: self._paint())
        self.bind("<Destroy>", self._untrace, add="+")
        self._bind_all(self)
        self._paint()

    def _untrace(self, e):
        if e.widget is self:
            try: self.var.trace_remove("write", self._trace)
            except (tk.TclError, ValueError): pass

    def _bind_all(self, w):
        w.bind("<Button-1>", self._toggle, add="+")
        w.configure(cursor="hand2")
        for child in w.winfo_children():
            self._bind_all(child)

    def _toggle(self, _e=None):
        self.var.set(not self.var.get())

    def _paint(self):
        try:
            on = bool(self.var.get())
        except tk.TclError:
            return
        self.set_colors(fill=C["accent_bg"] if on else C["card"],
                        outline=C["accent"] if on else C["border"])
        self.box.configure(bg=self.fill)
        self.box.delete("all")
        if on:
            rounded_rect(self.box, 1, 1, 21, 21, 6, fill=C["accent"], outline="")
            self.box.create_line(6, 11, 10, 15, 16, 7, fill="#FFFFFF", width=2, capstyle="round", joinstyle="round")
        else:
            rounded_rect(self.box, 1, 1, 21, 21, 6, fill=C["card"], outline=C["muted"])
