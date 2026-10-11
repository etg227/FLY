"""FLY 界面主题与自绘控件（参考 BakaXL 4.0 的布局：左侧导航栏 + 横幅 + 圆角卡片 + 大启动按钮）。

只依赖 tkinter，不引入第三方包，保证离线启动包照常可用。
"""
from __future__ import annotations
import math
import time
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


def autowrap(label, margin=0):
    """让 Label 的折行宽度跟随自身实际宽度（固定像素在窄窗口下会溢出）。"""
    label.bind("<Configure>", lambda e: label.configure(wraplength=max(60, e.width - margin)), add="+")
    return label


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


# ---- 动画 ---------------------------------------------------------------
# tkinter 没有动画系统，这里用 after() 逐帧重画。每个控件的动画按名字区分，
# 同名动画重新开始时会取消旧的；控件销毁时自动取消，避免回调打到已销毁的控件。

ANIMATIONS = True
FRAME_MS = 16


def ease_out(t):
    return 1 - (1 - t) ** 3


def _jobs(widget):
    jobs = widget.__dict__.get("_anim_jobs")
    if jobs is None:
        jobs = widget.__dict__["_anim_jobs"] = {}

        def cancel_all(e):
            # 销毁过程中 e.widget 可能只是路径字符串，所以按路径比较
            if str(e.widget) == str(widget):
                for job in list(jobs.values()):
                    try: widget.after_cancel(job)
                    except tk.TclError: pass
                jobs.clear()
        widget.bind("<Destroy>", cancel_all, add="+")
    return jobs


def stop_anim(widget, name):
    job = _jobs(widget).pop(name, None)
    if job:
        try: widget.after_cancel(job)
        except tk.TclError: pass


def animate(widget, name, duration, step, done=None, ease=ease_out):
    """在 duration 毫秒内以 step(progress) 逐帧推进，progress 已经过缓动，从 0 到 1。"""
    stop_anim(widget, name)
    jobs = _jobs(widget)
    if not ANIMATIONS or duration <= 0:
        step(1.0)
        if done: done()
        return
    start = time.perf_counter()

    def tick():
        jobs.pop(name, None)
        try:
            t = min(1.0, (time.perf_counter() - start) * 1000 / duration)
            step(ease(t))
            if t < 1.0:
                jobs[name] = widget.after(FRAME_MS, tick)
            elif done:
                done()
        except tk.TclError:
            pass
    tick()


def loop(widget, name, step, interval=33):
    """持续动画：step(已运行秒数)，直到 stop_anim(widget, name)。"""
    stop_anim(widget, name)
    jobs = _jobs(widget)
    start = time.perf_counter()

    def tick():
        jobs.pop(name, None)
        try:
            step(time.perf_counter() - start)
            jobs[name] = widget.after(interval, tick)
        except tk.TclError:
            pass
    tick()


def tween_color(widget, name, src, dst, duration, apply):
    """颜色渐变：apply(color) 每帧被调用。"""
    if src == dst:
        apply(dst); stop_anim(widget, name); return
    animate(widget, name, duration, lambda t: apply(_mix(src, dst, t)))


def slide_in(frame, distance=40, duration=220):
    """页面从右侧滑入（tkinter 控件不支持半透明，所以用位移代替淡入）。"""
    frame.place(x=distance, y=0, relwidth=1, relheight=1)
    frame.tkraise()
    animate(frame, "slide", duration,
            lambda t: frame.place_configure(x=round(distance * (1 - t))))


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

    def fade_to(self, fill, outline, duration=140):
        f0, o0 = self.fill, self.outline
        if (f0, o0) == (fill, outline):
            return
        animate(self, "colors", duration,
                lambda t: self.set_colors(fill=_mix(f0, fill, t), outline=_mix(o0, outline, t)))

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
    """自绘圆角按钮，兼容 .configure(state=..., text=...)，可选副标题（BakaXL 式大启动按钮）。

    busy=True 时禁用态显示为“进行中”：转圈图标 + （主按钮）流光，副标题换成 busy_text。
    """

    KINDS = {
        "accent":    (C["accent"], C["accent_hi"], C["accent_lo"], "#FFFFFF", "#DCE3F5"),
        "secondary": (C["card"], C["accent_bg"], "#DCE7FF", C["text"], C["card_alt"]),
        "danger":    (C["card"], "#FDECEC", "#FADADB", C["err"], C["card_alt"]),
        "ghost":     (C["nav"], C["nav_hi"], C["nav_sel"], C["nav_text"], C["nav"]),
    }

    def __init__(self, master, text="", command=None, kind="secondary", icon=None,
                 subtitle=None, width=120, height=38, radius=10, font=None, busy=False, **kw):
        bg = kw.pop("bg", None) or _bg_of(master)
        super().__init__(master, bg=bg, width=width, height=height,
                         highlightthickness=0, bd=0, cursor="hand2", **kw)
        self._text, self._icon, self._subtitle = text, icon, subtitle
        self._busy_text = None
        self._command = command
        self._kind = kind
        self._radius = radius
        self._font = font or FONTS["ui_b"]
        self._state = "normal"
        self._busy_capable = busy
        self._phase = 0.0
        self._hover = self._press = False
        self._fill = self._target_fill()
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, press=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(press=True))
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    @property
    def _busy(self):
        return self._busy_capable and self._state == "disabled"

    def _target_fill(self):
        base, hover, press, fg, disabled = self.KINDS[self._kind]
        if self._busy:
            return _mix(base, C["bg"], .3) if self._kind == "accent" else base
        if self._state == "disabled":
            return disabled
        return press if self._press else hover if self._hover else base

    def _set(self, hover=None, press=None):
        if hover is not None: self._hover = hover
        if press is not None: self._press = press
        self._fade(60 if self._press else 140)

    def _fade(self, duration):
        def apply(color):
            self._fill = color
            self._draw()
        tween_color(self, "fill", self._fill, self._target_fill(), duration, apply)

    def _release(self, e):
        fire = self._press and self._hover and self._state != "disabled"
        self._set(press=False)
        if fire and self._command:
            self._command()

    def _tick(self, elapsed):
        self._phase = elapsed
        self._draw()

    def _draw(self):
        base, hover, press, fg, disabled = self.KINDS[self._kind]
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            w, h = int(self.cget("width")), int(self.cget("height"))
        busy = self._busy
        off = self._state == "disabled" and not busy
        fill = self._fill
        color = C["muted"] if off else fg
        self.delete("all")
        raised = self._kind == "accent" and not off
        if raised:
            rounded_rect(self, 2, 4, w - 2, h, self._radius, fill=_mix(C["accent"], C["bg"], .55), outline="")
            rounded_rect(self, 1, 1, w - 1, h - 3, self._radius, fill=fill, outline="")
        else:
            outline = "" if self._kind == "ghost" else C["border"]
            rounded_rect(self, 1, 1, w - 1, h - 1, self._radius, fill=fill, outline=outline)
        bottom = h - 3 if raised else h - 1
        if busy and self._kind == "accent":
            # 流光：一条斜向亮带从左往右扫过，只在圆角之间的直边区域内绘制
            r = self._radius
            span = w - 2 * r
            band = max(24, w // 6)
            x = r + ((self._phase * 0.9) % 1.0) * (span + band) - band
            x0, x1 = max(r, x), min(w - r, x + band)
            if x1 - x0 > 12:
                self.create_polygon(x0 + 10, 1, x1, 1, x1 - 10, bottom, x0, bottom,
                                    fill=_mix(fill, "#FFFFFF", .18), outline="")
        cy = (h - 3) / 2 if raised else h / 2
        label = self._text
        sub = (self._busy_text or self._subtitle) if busy else self._subtitle
        ft = tkfont.Font(font=self._font)
        if busy:
            sr = 8 if sub else 7
            tw = ft.measure(label)
            sx = (w - tw) / 2 - sr - 10
            sy = cy - 9 if sub else cy
            self.create_arc(sx - sr, sy - sr, sx + sr, sy + sr, start=-self._phase * 360, extent=270,
                            style="arc", outline=color, width=2)
        if sub:
            self.create_text(w / 2, cy - 9, text=label, fill=color, font=self._font)
            self.create_text(w / 2, cy + 13, text=sub, fill=color if off else _mix(color, fill, .25),
                             font=FONTS["small"], width=w - 20)
        elif self._icon and not busy:
            iw = 22
            x0 = (w - iw - ft.measure(label)) / 2
            self.create_text(x0 + iw / 2 - 2, cy, text=ICONS.get(self._icon, ""), fill=color, font=FONTS["icon_s"])
            self.create_text(x0 + iw, cy, text=label, fill=color, font=self._font, anchor="w")
        else:
            self.create_text(w / 2, cy, text=label, fill=color, font=self._font)

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        changed = False
        old_state = self._state
        for key, attr in (("state", "_state"), ("text", "_text"), ("subtitle", "_subtitle"),
                          ("busy_text", "_busy_text"), ("command", "_command")):
            if key in kw:
                setattr(self, attr, kw.pop(key)); changed = True
        result = super().configure(**kw) if kw else None
        if changed:
            if self._state != old_state:
                super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
                if self._busy:
                    loop(self, "busy", self._tick)
                else:
                    stop_anim(self, "busy")
                self._fade(160)
            else:
                self._draw()
        return result
    config = configure

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
        halo = self.create_oval(17, 15, 17, 15, outline="", fill="")
        dot = self.create_oval(13, 11, 21, 19, fill=color, outline="")
        # 加速中：慢速呼吸 + 扩散光圈；进行中（启动/检测等）：快速闪烁；其余静止
        if color in (C["ok"], C["warn"]):
            period = 1.8 if color == C["ok"] else 0.8

            def pulse(elapsed):
                k = (elapsed % period) / period
                if color == C["ok"]:
                    r = 4 + 7 * k
                    self.coords(halo, 17 - r, 15 - r, 17 + r, 15 + r)
                    self.itemconfigure(halo, fill=_mix(color, fill, .45 + .55 * k))
                else:
                    self.itemconfigure(dot, fill=_mix(color, fill, .6 * (0.5 + 0.5 * math.cos(k * 2 * math.pi))))
            loop(self, "pulse", pulse, 33)
        else:
            stop_anim(self, "pulse")
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
        self._side = None

    def bind_side(self, label, value_var, sub_label, sub_var):
        """右侧信息块（当前节点 / 延迟），跟随 StringVar 实时重画。"""
        self._side = (label, value_var, sub_label, sub_var)
        for var in (value_var, sub_var):
            var.trace_add("write", lambda *_: self.after_idle(self._draw))
        self._draw()

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
        self.create_text(32, 46, text=self.title, anchor="w", fill="#FFFFFF", font=FONTS["h1"])
        if self.badge:
            ft = tkfont.Font(font=FONTS["small"])
            tw = tkfont.Font(font=FONTS["h1"]).measure(self.title)
            bx = 32 + tw + 14
            bw = ft.measure(self.badge) + 18
            rounded_rect(self, bx, 34, bx + bw, 58, 11, fill=_mix(C["accent"], "#FFFFFF", .25), outline="")
            self.create_text(bx + bw / 2, 46, text=self.badge, fill="#FFFFFF", font=FONTS["small"])
        side_w = 270 if self._side else 0
        self.create_text(32, 84, text=self.subtitle, anchor="nw", fill=_mix("#FFFFFF", C["accent"], .12),
                         font=FONTS["ui"], width=max(200, w - 64 - side_w))
        if self._side:
            label, var, sub_label, sub_var = self._side
            try:
                value, sub = var.get(), sub_var.get()
            except tk.TclError:
                value, sub = "-", "-"
            ft = tkfont.Font(font=FONTS["h3"])
            if ft.measure(value) > side_w - 20:
                while len(value) > 1 and ft.measure(value + "…") > side_w - 20:
                    value = value[:-1]
                value += "…"
            x = w - 26
            soft = _mix("#FFFFFF", C["accent2"], .2)
            self.create_text(x, 62, text=label, anchor="ne", fill=soft, font=FONTS["small"])
            self.create_text(x, 80, text=value, anchor="ne", fill="#FFFFFF", font=FONTS["h3"])
            self.create_text(x, 108, text=f"{sub_label} {sub}", anchor="ne", fill=soft, font=FONTS["ui"])
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
        self.ic.configure(fg=C["accent_hi"] if self.selected else fg)
        self.lb.configure(fg=fg, font=FONTS["ui_b"] if self.selected else FONTS["ui"])

        def apply(color):
            for w in (self, self.ic, self.lb):
                w.configure(bg=color)
            self.bar.configure(bg=C["accent"] if self.selected else color)
        tween_color(self, "bg", self.cget("bg"), bg, 150, apply)
        if self.selected:
            # 选中指示条从中间“长”出来
            animate(self.bar, "grow", 200, lambda t: self.bar.configure(height=max(2, round(22 * t))))


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
        self._pos = 1.0 if variable.get() else 0.0
        self._trace = variable.trace_add("write", lambda *_: self._slide())
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

    def _slide(self):
        try:
            target = 1.0 if self.var.get() else 0.0
        except tk.TclError:
            return
        start = self._pos

        def step(t):
            self._pos = start + (target - start) * t
            self._draw()
        animate(self.sw, "slide", 180, step)

    def _draw(self):
        cv, p = self.sw, self._pos
        cv.delete("all")
        rounded_rect(cv, 1, 1, 39, 21, 10, fill=_mix(C["border"], C["accent"], p), outline="")
        x = 11 + 18 * p
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
        self._hover = False
        self._trace = variable.trace_add("write", lambda *_: self._paint())
        self.bind("<Destroy>", self._untrace, add="+")
        self._bind_all(self)
        self._paint(instant=True)

    def _untrace(self, e):
        if e.widget is self:
            try: self.var.trace_remove("write", self._trace)
            except (tk.TclError, ValueError): pass

    def _bind_all(self, w):
        w.bind("<Button-1>", self._toggle, add="+")
        w.bind("<Enter>", lambda e: self._set_hover(True), add="+")
        w.bind("<Leave>", lambda e: self.after(10, self._check_leave), add="+")
        w.configure(cursor="hand2")
        for child in w.winfo_children():
            self._bind_all(child)

    def _toggle(self, _e=None):
        self.var.set(not self.var.get())

    def set_colors(self, fill=None, outline=None):
        super().set_colors(fill=fill, outline=outline)
        self.box.configure(bg=self.fill)

    def _set_hover(self, value):
        if value != self._hover:
            self._hover = value
            self._paint()

    def _check_leave(self):
        # 在卡片内部的子控件之间移动也会触发 <Leave>，以指针实际位置为准
        try:
            under = self.winfo_containing(*self.winfo_pointerxy())
        except (tk.TclError, KeyError):
            under = None
        me = str(self)
        if under is None or not (str(under) == me or str(under).startswith(me + ".")):
            self._set_hover(False)

    def _paint(self, instant=False):
        try:
            on = bool(self.var.get())
        except tk.TclError:
            return
        fill = C["accent_bg"] if on else C["card_alt"] if self._hover else C["card"]
        outline = C["accent"] if on else C["accent_hi"] if self._hover else C["border"]
        if instant:
            self.set_colors(fill=fill, outline=outline)
        else:
            self.fade_to(fill, outline)
        self.box.delete("all")
        if on:
            rounded_rect(self.box, 1, 1, 21, 21, 6, fill=C["accent"], outline="")
            self.box.create_line(6, 11, 10, 15, 16, 7, fill="#FFFFFF", width=2, capstyle="round", joinstyle="round")
        else:
            rounded_rect(self.box, 1, 1, 21, 21, 6, fill=C["card"], outline=C["muted"])


# ---- 通知（替代弹窗） ---------------------------------------------------

def _draw_glyph(cv, kind, cx, cy, color):
    """自绘通知图标，不依赖图标字体。"""
    w = 2
    if kind == "ok":
        cv.create_line(cx - 5, cy, cx - 1, cy + 4, cx + 5, cy - 4, fill=color, width=w,
                       capstyle="round", joinstyle="round")
    elif kind == "err":
        cv.create_line(cx - 4, cy - 4, cx + 4, cy + 4, fill=color, width=w, capstyle="round")
        cv.create_line(cx - 4, cy + 4, cx + 4, cy - 4, fill=color, width=w, capstyle="round")
    elif kind == "warn":
        cv.create_line(cx, cy - 5, cx, cy + 1, fill=color, width=w, capstyle="round")
        cv.create_oval(cx - 1.3, cy + 3.5, cx + 1.3, cy + 6, fill=color, outline="")
    else:
        cv.create_oval(cx - 1.3, cy - 6, cx + 1.3, cy - 3.5, fill=color, outline="")
        cv.create_line(cx, cy - 1, cx, cy + 5, fill=color, width=w, capstyle="round")


class Toast(tk.Canvas):
    KINDS = {"info": C["accent"], "ok": C["ok"], "warn": C["warn"], "err": C["err"]}
    WIDTH = 340

    def __init__(self, host, text, kind, title, duration):
        super().__init__(host.win, width=self.WIDTH, height=60, bg=_bg_of(host.win),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.host, self.kind = host, kind if kind in self.KINDS else "info"
        self.duration, self.left = duration, duration
        self.y = 0.0
        self._hover = False
        self._closing = False
        color = self.KINDS[self.kind]
        tx = 54
        self.create_text(tx, 20 if title else 0, text=text, anchor="nw", width=self.WIDTH - tx - 34,
                                font=FONTS["ui"], fill=C["text"])
        if title:
            self.create_text(tx, 0, text=title, anchor="nw", font=FONTS["ui_b"], fill=C["text"], tags="title")
        x0, y0, x1, y1 = self.bbox("all")
        h = max(56, (y1 - y0) + 30)
        self.delete("all")
        self.configure(height=h + 4)
        rounded_rect(self, 3, 4, self.WIDTH - 1, h + 3, 12, fill=_mix(C["text"], C["bg"], .9), outline="")
        rounded_rect(self, 1, 1, self.WIDTH - 3, h, 12, fill=C["card"], outline=C["border"])
        self.create_oval(16, h / 2 - 13, 42, h / 2 + 13, fill=_mix(color, "#FFFFFF", .85), outline="")
        _draw_glyph(self, self.kind, 29, h / 2, color)
        top = (h - (y1 - y0)) / 2
        if title:
            self.create_text(tx, top, text=title, anchor="nw", font=FONTS["ui_b"], fill=C["text"])
            top += 20
        self.create_text(tx, top, text=text, anchor="nw", width=self.WIDTH - tx - 34,
                         font=FONTS["ui"], fill=C["text"] if not title else C["subtext"])
        self.create_text(self.WIDTH - 20, 14, text="✕", font=FONTS["small"], fill=C["muted"])
        self._bar = self.create_line(14, h - 2, self.WIDTH - 17, h - 2, fill=color, width=2, capstyle="round")
        self._bar_span = (14, self.WIDTH - 17, h - 2)
        self.height = h + 4
        self.bind("<Enter>", lambda e: setattr(self, "_hover", True))
        self.bind("<Leave>", lambda e: setattr(self, "_hover", False))
        self.bind("<Button-1>", lambda e: self.dismiss())
        self._last = time.perf_counter()
        loop(self, "life", self._tick, 33)

    def _tick(self, _elapsed):
        now = time.perf_counter()
        if not self._hover and not self._closing:
            self.left -= (now - self._last) * 1000
        self._last = now
        x0, x1, y = self._bar_span
        frac = max(0.0, self.left / self.duration)
        self.coords(self._bar, x0, y, x0 + (x1 - x0) * frac, y)
        if self.left <= 0 and not self._closing:
            self.dismiss()

    def dismiss(self):
        if self._closing:
            return
        self._closing = True
        stop_anim(self, "life")
        start = float(self.place_info().get("x", -self.host.right) or -self.host.right)

        def step(t):
            self.place_configure(x=round(start + (self.WIDTH + 40) * t))
        animate(self, "slide", 220, step, done=self._gone, ease=lambda t: t * t)

    def _gone(self):
        self.host._remove(self)
        self.destroy()


class ToastHost:
    """窗口右下角的通知栈：新通知从右侧滑入，旧通知向上让位，到时自动滑出。"""

    MAX = 4
    DURATION = {"info": 3200, "ok": 3200, "warn": 5000, "err": 8000}

    def __init__(self, win, right=16, bottom=16):
        self.win, self.right, self.bottom = win, right, bottom
        self.toasts = []
        win.__dict__["_toast_host"] = self

    @classmethod
    def of(cls, win):
        # 对话框右下角通常是“保存”按钮，通知要停在按钮上方，不能挡住它
        return win.__dict__.get("_toast_host") or cls(win, right=16, bottom=60)

    def show(self, text, kind="info", title=None, duration=None):
        text = str(text)
        if duration is None:
            duration = self.DURATION.get(kind, 3200) + min(6000, 40 * max(0, len(text) - 40))
        t = Toast(self, text, kind, title, duration)
        t.y = -self.bottom
        t.place(relx=1.0, rely=1.0, anchor="se", x=Toast.WIDTH + 30, y=t.y)
        tk.Misc.tkraise(t)  # Canvas.lift 是画布图元的 raise，不是控件层级
        self.toasts.insert(0, t)
        animate(t, "slide", 320, lambda p: t.place_configure(x=round(Toast.WIDTH + 30 - (Toast.WIDTH + 30 + self.right) * p)))
        while len([x for x in self.toasts if not x._closing]) > self.MAX:
            [x for x in self.toasts if not x._closing][-1].dismiss()
        self._restack()
        return t

    def _remove(self, toast):
        if toast in self.toasts:
            self.toasts.remove(toast)
            self._restack()

    def _restack(self):
        y = -self.bottom
        for t in self.toasts:
            target, start = y, t.y
            if abs(target - start) > 0.5:
                def step(p, t=t, start=start, target=target):
                    t.y = start + (target - start) * p
                    t.place_configure(y=round(t.y))
                animate(t, "stack", 240, step)
            y -= t.height + 8


def toast(win, text, kind="info", title=None, duration=None):
    try:
        return ToastHost.of(win).show(text, kind, title, duration)
    except tk.TclError:
        return None


# ---- 数字滚动 -----------------------------------------------------------

class CountLabel(tk.Label):
    """数值变化时从旧值滚动到新值。"""

    def __init__(self, master, fmt, value=0.0, **kw):
        super().__init__(master, **kw)
        self.fmt, self.value = fmt, float(value)
        self.configure(text=fmt(self.value))

    def set_value(self, value, duration=600):
        value = float(value)
        start = self.value
        self.value = value
        if start == value:
            self.configure(text=self.fmt(value)); return
        animate(self, "count", duration, lambda t: self.configure(text=self.fmt(start + (value - start) * t)))

    def set_text(self, text):
        stop_anim(self, "count")
        self.configure(text=text)


# ---- 实时速率曲线 -------------------------------------------------------

class SpeedChart(tk.Frame):
    """下行（面积）+ 上行（折线）实时速率图，新采样到来时平滑向左滚动。"""

    N = 30

    def __init__(self, master, fmt_speed, interval_ms=2000, height=74):
        bg = _bg_of(master)
        super().__init__(master, bg=bg)
        self.fmt, self.interval = fmt_speed, interval_ms
        head = tk.Frame(self, bg=bg); head.pack(fill="x")
        dot = lambda: tk.Canvas(head, width=10, height=10, bg=bg, highlightthickness=0)
        d1 = dot(); d1.create_oval(1, 1, 9, 9, fill=C["accent"], outline=""); d1.pack(side="left")
        self.down = CountLabel(head, lambda v: "↓ " + fmt_speed(v), font=FONTS["value"], bg=bg, fg=C["text"])
        self.down.pack(side="left", padx=(6, 18))
        d2 = dot(); d2.create_oval(1, 1, 9, 9, fill=C["accent2"], outline=""); d2.pack(side="left")
        self.up = CountLabel(head, lambda v: "↑ " + fmt_speed(v), font=FONTS["ui_b"], bg=bg, fg=C["subtext"])
        self.up.pack(side="left", padx=(6, 0))
        self.cv = tk.Canvas(self, height=height, bg=bg, highlightthickness=0, bd=0)
        self.cv.pack(fill="x", pady=(8, 0))
        self.cv.bind("<Configure>", lambda e: self._draw())
        self.samples = []
        self.offset = 0.0        # 0..1，新点滑入的进度
        self.scale = 1.0
        self.active = False
        self.idle()

    def idle(self):
        self.active = False
        self.samples = []
        stop_anim(self.cv, "scroll")
        self.down.set_text("↓ —"); self.up.set_text("↑ —")
        self._draw()

    def push(self, down, up):
        down, up = max(0.0, float(down)), max(0.0, float(up))
        if not self.active:
            self.active = True
            self.samples = [(0.0, 0.0)] * self.N
            self.down.value = self.up.value = 0.0
        self.samples = (self.samples + [(down, up)])[-(self.N + 1):]
        self.down.set_value(down, 700)
        self.up.set_value(up, 700)
        target = max([max(d, u) for d, u in self.samples] + [32 * 1024]) * 1.25
        s0 = self.scale

        def step(t):
            self.offset = t
            self.scale = s0 + (target - s0) * min(1.0, t * 2.5)
            self._draw()
        # 线性滚动、时长略短于采样间隔，保证一次滚完再来下一个点
        animate(self.cv, "scroll", int(self.interval * 0.92), step, ease=lambda t: t)

    def _draw(self):
        cv = self.cv
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 10:
            return
        cv.delete("all")
        pad = 4
        for frac in (0.0, 0.5, 1.0):
            y = pad + (h - 2 * pad) * frac
            cv.create_line(0, y, w, y, fill=C["border"] if frac == 1.0 else C["card_alt"])
        if not self.active or len(self.samples) < 2:
            cv.create_text(w / 2, h / 2, text="开始加速后显示实时速率", fill=C["muted"], font=FONTS["small"])
            return
        cv.create_text(w - 2, pad + 1, text=self.fmt(self.scale / 1.25), anchor="ne", fill=C["muted"],
                       font=FONTS["small"])
        step = w / (self.N - 1)
        n = len(self.samples)
        shift = step * (1 - self.offset)

        def pts(idx):
            out = []
            for i, s in enumerate(self.samples):
                x = w - (n - 1 - i) * step + shift
                y = h - pad - (h - 2 * pad) * min(1.0, s[idx] / self.scale)
                out += [x, y]
            return out
        down = pts(0)
        cv.create_polygon([down[0], h] + down + [down[-2], h], fill=_mix(C["accent"], C["card"], .82),
                          outline="", smooth=True)
        cv.create_line(down, fill=C["accent"], width=2, smooth=True)
        cv.create_line(pts(1), fill=C["accent2"], width=1.5, smooth=True, dash=(4, 3))
        x, y = down[-2], down[-1]
        if x <= w:
            cv.create_oval(x - 3.5, y - 3.5, x + 3.5, y + 3.5, fill=C["card"], outline=C["accent"], width=2)


# ---- 订阅用量条 ---------------------------------------------------------

class QuotaBars(tk.Frame):
    """每条订阅一根进度条（已用比例），填充动画从当前值过渡到新值。"""

    def __init__(self, master, fmt_bytes):
        super().__init__(master, bg=_bg_of(master))
        self.fmt = fmt_bytes
        self._rows = []
        self._ratios = {}

    def message(self, text):
        self._clear()
        tk.Label(self, text=text, font=FONTS["small"], bg=self.cget("bg"), fg=C["muted"],
                 anchor="w").pack(fill="x")

    def _clear(self):
        for w in self.winfo_children():
            w.destroy()

    def set(self, items):
        """items: [(tag, quota_dict_or_None, reason_text)]"""
        self._clear()
        bg = self.cget("bg")
        for i, (tag, q, reason) in enumerate(items):
            row = tk.Frame(self, bg=bg); row.pack(fill="x", pady=(0 if i == 0 else 8, 0))
            top = tk.Frame(row, bg=bg); top.pack(fill="x")
            if not q:
                tk.Label(top, text=(tag + " " if tag else "") + reason, font=FONTS["small"], bg=bg,
                         fg=C["muted"], anchor="w").pack(side="left")
                continue
            if q["ratio"] is None:
                left = f"已用 {self.fmt(q['used'])}（不限量）"
            else:
                left = f"剩余 {self.fmt(q['remaining'])} / {self.fmt(q['total'])}"
            color = C["err"] if q["low"] else C["accent"]
            tk.Label(top, text=(tag + " " if tag else "") + left, font=FONTS["small"], bg=bg,
                     fg=C["err"] if q["low"] else C["text"], anchor="w").pack(side="left")
            if q["expire"]:
                try:
                    exp = "到期 " + time.strftime("%Y-%m-%d", time.localtime(q["expire"]))
                except (OverflowError, OSError, ValueError):
                    exp = ""
                tk.Label(top, text=exp, font=FONTS["small"], bg=bg, fg=C["muted"]).pack(side="right")
            if q["ratio"] is None:
                continue
            bar = tk.Canvas(row, height=8, bg=bg, highlightthickness=0, bd=0)
            bar.pack(fill="x", pady=(5, 0))
            key = tag or "_"
            start = self._ratios.get(key, 0.0)
            self._ratios[key] = q["ratio"]
            state = {"r": start}

            def draw(_e=None, bar=bar, state=state, color=color):
                w = bar.winfo_width()
                bar.delete("all")
                rounded_rect(bar, 0, 0, w, 8, 4, fill=C["card_alt"], outline=C["border"])
                fw = max(8.0, w * state["r"]) if state["r"] > 0 else 0
                if fw:
                    rounded_rect(bar, 0, 0, fw, 8, 4, fill=color, outline="")
            bar.bind("<Configure>", draw)

            def step(t, state=state, start=start, end=q["ratio"], draw=draw):
                state["r"] = start + (end - start) * t
                draw()
            animate(bar, "fill", 900, step)


# ---- 用量统计 -----------------------------------------------------------

class StatTile(tk.Frame):
    def __init__(self, master, title, fmt_bytes):
        bg = _bg_of(master)
        super().__init__(master, bg=bg)
        self.fmt = fmt_bytes
        tk.Label(self, text=title, font=FONTS["small"], bg=bg, fg=C["subtext"], anchor="w").pack(fill="x")
        self.main = CountLabel(self, fmt_bytes, font=FONTS["value"], bg=bg, fg=C["text"], anchor="w")
        self.main.pack(fill="x", pady=(2, 0))
        self.sub = CountLabel(self, lambda v: "直连 " + fmt_bytes(v), font=FONTS["small"], bg=bg,
                              fg=C["muted"], anchor="w")
        self.sub.pack(fill="x")

    def set(self, proxied, direct):
        self.main.set_value(proxied)
        self.sub.set_value(direct)


class MiniBars(tk.Canvas):
    """近 7 天走日本节点的用量柱状图；数值变化时柱子平滑长高/缩短。"""

    WEEK = "一二三四五六日"

    def __init__(self, master, fmt_bytes, height=62):
        super().__init__(master, height=height, bg=_bg_of(master), highlightthickness=0, bd=0)
        self.fmt = fmt_bytes
        self.days = []
        self.shown = []
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, days):
        """days: [(YYYY-MM-DD, proxied, direct)]"""
        old = self.shown if len(self.shown) == len(days) else [0.0] * len(days)
        new = [float(p) for _, p, _ in days]
        self.days = days
        if old == new:
            self.shown = new; self._draw(); return

        def step(t):
            self.shown = [a + (b - a) * t for a, b in zip(old, new)]
            self._draw()
        animate(self, "grow", 700, step)

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("all")
        if w < 10 or not self.days:
            return
        n = len(self.days)
        top, label_h = 4, 16
        area = h - top - label_h
        peak = max(max(self.shown or [0]), 1.0)
        slot = w / n
        bw = min(26, slot * 0.56)
        for i, (key, _, _) in enumerate(self.days):
            cx = slot * i + slot / 2
            v = self.shown[i] if i < len(self.shown) else 0
            bh = max(3.0, area * v / peak) if v > 0 else 3.0
            today = i == n - 1
            fill = C["accent"] if today else _mix(C["accent"], C["card"], .62)
            rounded_rect(self, cx - bw / 2, top + area - bh, cx + bw / 2, top + area, 4, fill=fill, outline="")
            try:
                wd = time.strptime(key, "%Y-%m-%d").tm_wday
                lab = "今" if today else self.WEEK[wd]
            except ValueError:
                lab = ""
            self.create_text(cx, h - label_h / 2, text=lab, font=FONTS["small"],
                             fill=C["accent"] if today else C["muted"])
