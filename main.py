from __future__ import annotations
import argparse, json, os, queue, threading, time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from backend.config import (
    Paths, ensure_private_files, list_routing_profiles, profile_from_url,
    load_app_settings, load_node_source, load_custom_profiles, save_custom_profiles,
    node_source_is_configured, save_json, app_version, validate_profile,
    drain_config_warnings, profile_has_effect, update_app_settings
)
from backend.core_installer import (
    VALID, MISSING, REPAIRABLE, INVALID, TRANSIENT,
    install_core as download_core,
)
from backend.core_manager import CoreManager, DialFailureTracker
from backend.launcher import log_hints
from backend.instance_lock import SingleInstance
from backend.mihomo_api import MihomoApi, JapanNodeSelector
from backend.privacy import redact_log_line
from backend.subscription import check_subscription, describe_userinfo, fmt_bytes, fmt_speed
from backend.system_proxy import SystemProxy
from backend.self_update import schedule_launcher_replace
from backend.windows_admin import is_admin, relaunch_as_admin

APP_DIR = Path(__file__).resolve().parent
PATHS = Paths(APP_DIR)

class _Cancelled(Exception):
    """启动流程被“停止”或关窗打断。"""

def _needs_tun(profile):
    return (bool(profile.get("full_browser")) or
            str(profile.get("launch_mode","browser")).lower() == "tun" or
            bool(profile.get("processes")))

class FlyApp:
    def __init__(self, root, initial_games=None, autostart=False):
        ensure_private_files(PATHS)
        self.root = root
        self.version = app_version(PATHS)
        self.root.title(f"FLY v{self.version} - Selective Routing")
        self.root.geometry("900x860")
        self.root.minsize(820, 760)

        self.logs = queue.Queue()
        self._log_lock = threading.Lock()
        self._log_file = None
        self._log_path = PATHS.runtime / "fly.log"
        self._log_pending = 0
        try:
            PATHS.runtime.mkdir(parents=True, exist_ok=True)
            if self._log_path.exists() and self._log_path.stat().st_size > 2 * 1024 * 1024:
                old = PATHS.runtime / "fly.log.1"
                try: old.unlink(missing_ok=True)
                except OSError: pass
                self._log_path.replace(old)
            self._log_file = open(self._log_path, "a", encoding="utf-8")
            self._log_file.write(f"\n===== FLY v{self.version} 会话开始 {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            self._log_file.flush()
        except OSError:
            pass
        schedule_launcher_replace(PATHS.app, self.log)
        self.core = CoreManager(PATHS, self.log, on_exit=self._on_core_exit,
                                on_line=self._on_core_line)
        self.sysproxy = SystemProxy(PATHS.runtime / "sysproxy_backup.json", self.log)
        self.selector = None
        self.sysproxy_active = False
        self._watch_stop = None
        self._traffic_stop = None
        self._core_installing = False
        self._core_verifying = False
        self._core_verify_retry_after = 0.0
        self._autostart_deadline = time.time() + 30 if autostart else 0.0
        # 启动是个几十秒的后台流程，中途可能被“停止”或关窗打断。
        # 没有取消机制的话，worker 会在窗口销毁之后才去开系统代理，
        # 于是代理永久指向一个死端口——浏览器断网到下次启动 FLY。
        self._start_token = 0
        self._start_thread = None
        self._start_cancel = threading.Event()
        self._closing = False
        self._proxy_lock = threading.Lock()
        self._selection_lock = threading.Lock()
        # 节点被墙时内核每隔几秒就报一次出站失败，而看门狗要 60 秒 x 3 轮
        # 才会换节点——中间这三分钟界面显示“加速中”，其实什么都打不开。
        # 用内核自己的失败日志把看门狗立刻叫醒。
        self._probe_now = threading.Event()
        self._dial_tracker = DialFailureTracker("FLY-JP")
        self._sub_fetching = False
        self._sub_last = 0.0

        self.profiles = []
        self.profile_by_id = {}
        self.profile_vars = {}
        self.always_on = []
        self.initial_ids = set(initial_games or [])

        app = load_app_settings(PATHS)
        self.services_var = tk.BooleanVar(value=bool(app.get("services_enabled", True)))
        self.status_var = tk.StringVar(value="已停止")
        self.core_var = tk.StringVar()
        self.source_var = tk.StringVar()
        self.node_var = tk.StringVar(value="-")
        self.delay_var = tk.StringVar(value="-")
        self.sub_var = tk.StringVar(value="-")
        self.traffic_var = tk.StringVar(value="-")

        self.build()
        self.reload_profiles(first=True)
        self.refresh_status()
        self.root.after(100, self.flush_logs)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.log(f"FLY v{self.version} 就绪。")
        self.log("安全原则：未命中所选业务规则的连接最终 MATCH,DIRECT；TUN 模式 DNS 由本地 Mihomo 接管，DIRECT 出口使用系统解析。")
        self.log("[LOG] runtime\\fly.log 为自动脱敏诊断日志，可用于反馈；不要发送 private\\ 目录。")
        for warning in drain_config_warnings():
            self.log("[CONFIG] " + warning)
        self.sysproxy.restore_orphan()
        threading.Thread(target=self.core.cleanup_orphans,daemon=True).start()
        if autostart:
            self.log("[FLY] Autostart requested (admin relaunch).")
            self.root.after(300, self._autostart_when_core_ready)

    def build(self):
        outer = ttk.Frame(self.root, padding=16); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=f"FLY v{self.version}", font=("Segoe UI",21,"bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="使用你自己的 Clash/Mihomo 节点，只让勾选的区域限定应用/服务走日本线路，其余流量保持本地直连。",
            wraplength=840
        ).pack(anchor="w", pady=(0,12))

        back = ttk.LabelFrame(outer, text="后端", padding=10); back.pack(fill="x", pady=(0,10))
        for i, (label, var) in enumerate([("内核：",self.core_var),("节点来源：",self.source_var),("日本节点：",self.node_var)]):
            r = ttk.Frame(back); r.pack(fill="x", pady=(0 if i==0 else 8,0))
            ttk.Label(r,text=label,width=14).pack(side="left")
            ttk.Label(r,textvariable=var,font=("Segoe UI",10,"bold") if i==2 else None).pack(side="left")
            if i==1:
                ttk.Button(r,text="节点 / 应用设置",command=self.open_settings).pack(side="right")
            elif i==2:
                ttk.Label(r,text="延迟：").pack(side="left", padx=(18,4))
                ttk.Label(r,textvariable=self.delay_var).pack(side="left")
        for label, var in [("订阅流量：",self.sub_var),("实时流量：",self.traffic_var)]:
            r = ttk.Frame(back); r.pack(fill="x", pady=(8,0))
            ttk.Label(r,text=label,width=14).pack(side="left")
            ttk.Label(r,textvariable=var).pack(side="left")

        prof = ttk.LabelFrame(outer,text="分流配置（可多选，同时加速）",padding=12)
        prof.pack(fill="x", pady=(0,10))
        self.profiles_frame = ttk.Frame(prof); self.profiles_frame.pack(fill="x")
        self.services_cb = ttk.Checkbutton(prof, variable=self.services_var,
                                           command=self._services_toggled,
                                           text="默认加速常用服务")
        self.services_cb.pack(anchor="w", pady=(8,0))
        actions = ttk.Frame(prof); actions.pack(fill="x", pady=(8,0))
        ttk.Button(actions,text="添加/管理加速网站...",command=self.open_custom_sites).pack(side="left")
        ttk.Label(actions,text="填个网址就能加自己的加速项，只保存在本机 private\\。").pack(side="left", padx=(10,0))

        buttons = ttk.Frame(outer); buttons.pack(fill="x",pady=(2,12))
        self.start_btn = ttk.Button(buttons,text="一键加速",command=self.start_accel); self.start_btn.pack(side="left")
        ttk.Button(buttons,text="停止",command=self.stop_accel).pack(side="left",padx=8)
        self.switch_btn = ttk.Button(buttons,text="换日本节点",command=self.switch_node)
        self.switch_btn.pack(side="left",padx=8)
        ttk.Button(buttons,text="刷新状态",command=self.refresh_status).pack(side="left",padx=8)
        ttk.Label(buttons,text="状态：").pack(side="right")
        ttk.Label(buttons,textvariable=self.status_var,font=("Segoe UI",10,"bold")).pack(side="right",padx=(0,5))

        route = ttk.LabelFrame(outer,text="分流保证",padding=10); route.pack(fill="x",pady=(0,10))
        ttk.Label(route,justify="left",wraplength=840,text=(
            "域名 / IP / 端口 / 进程规则全部显式声明，未命中业务规则的连接最终 MATCH,DIRECT；局域网/链路本地地址强制 DIRECT。\n"
            "标注“整浏览器”的配置使用 TUN 按浏览器进程匹配，不再把所有使用系统代理的软件一起送去日本线路。\n"
            "TUN 模式会由本机 Mihomo 接管 DNS，但 DIRECT 连接使用系统 DNS 重新解析；纯域名配置才使用 Windows 系统代理，停止或异常退出会自动还原。"
        )).pack(anchor="w")

        logf = ttk.LabelFrame(outer,text="Live log",padding=8); logf.pack(fill="both",expand=True)
        self.logbox = tk.Text(logf,wrap="word",font=("Consolas",9),height=14); self.logbox.pack(side="left",fill="both",expand=True)
        sb=ttk.Scrollbar(logf,orient="vertical",command=self.logbox.yview); sb.pack(side="right",fill="y"); self.logbox.configure(yscrollcommand=sb.set)

    def reload_profiles(self, first=False):
        previous = {pid for pid,var in self.profile_vars.items() if var.get()}
        self.profiles = list_routing_profiles(PATHS)
        self.profile_by_id = {r["id"]:r for r in self.profiles}
        self.always_on = [p for p in self.profiles if p.get("always_on")]
        selectable = [p for p in self.profiles if not p.get("always_on")]
        names = "、".join(p["name"] for p in self.always_on)
        self.services_cb.configure(
            text=f"默认加速常用服务：{names}" if names else "默认加速常用服务（无内置服务配置）")
        for child in self.profiles_frame.winfo_children():
            child.destroy()
        self.profile_vars = {}
        initial = self.initial_ids if first else previous
        if first and not initial and selectable:
            initial = {selectable[0]["id"]}
        for i, profile in enumerate(selectable):
            pid = profile["id"]
            var = tk.BooleanVar(value=pid in initial)
            self.profile_vars[pid] = var
            source = "自定义" if profile.get("source")=="custom" else ""
            mode = "TUN" if _needs_tun(profile) else "域名"
            if profile.get("full_browser"):
                mode = "整浏览器"
            text = f"{profile['name']}  [{mode}{' / '+source if source else ''}]"
            ttk.Checkbutton(self.profiles_frame,text=text,variable=var).grid(
                row=i//2,column=i%2,sticky="w",padx=(0,24),pady=2
            )

    def selected_profiles(self):
        return [r["id"] for r in self.profiles if self.profile_vars.get(r["id"]) and self.profile_vars[r["id"]].get()]

    def _services_toggled(self):
        update_app_settings(PATHS, {"services_enabled": bool(self.services_var.get())})
        state="开启" if self.services_var.get() else "关闭"
        self.log(f"[SERVICES] 常用服务默认加速已{state}。")
        if self.core.is_running():
            self.log("[SERVICES] 正在加速中，重新点“一键加速”后生效。")

    def _rotate_log_locked(self):
        if not self._log_file or not self._log_path:
            return
        try:
            if self._log_path.stat().st_size <= 2 * 1024 * 1024:
                return
            self._log_file.flush(); self._log_file.close()
            old = PATHS.runtime / "fly.log.1"
            try: old.unlink(missing_ok=True)
            except OSError: pass
            self._log_path.replace(old)
            self._log_file = open(self._log_path, "a", encoding="utf-8")
            self._log_pending = 0
        except OSError:
            pass

    def log(self,msg):
        line=f"{time.strftime('%H:%M:%S')} {msg}"
        self.logs.put(line)
        if self._log_file:
            try:
                with self._log_lock:
                    self._rotate_log_locked()
                    safe = redact_log_line(time.strftime("%m-%d ")+line)
                    self._log_file.write(safe+"\n")
                    self._log_pending += 1
                    if self._log_pending >= 20 or "[ERROR]" in line or "异常" in line:
                        self._log_file.flush(); self._log_pending = 0
            except OSError:
                pass

    def flush_logs(self):
        try:
            while True:
                x=self.logs.get_nowait()
                self.logbox.insert("end",x+"\n")
            # unreachable
        except queue.Empty:
            pass
        try:
            rows = int(self.logbox.index("end-1c").split(".")[0])
            if rows > 2000:
                self.logbox.delete("1.0", f"{rows-1800}.0")
            self.logbox.see("end")
        except Exception:
            pass
        if not self._closing:
            self.root.after(100,self.flush_logs)

    def refresh_status(self):
        cached = self.core.verification_detail()
        if self._core_installing:
            self.core_var.set("自动下载/修复中...")
        elif cached and cached.state == VALID:
            self.core_var.set("就绪")
        elif cached and cached.state in (MISSING, REPAIRABLE, INVALID):
            self.core_var.set("需要修复")
            self._ensure_core()
        elif cached and cached.state == TRANSIENT:
            self.core_var.set("暂时无法校验")
        else:
            self.core_var.set("校验中...")
            if not self._core_verifying:
                self._core_verifying = True
                threading.Thread(target=self._verify_core_worker, daemon=True).start()

        ok,detail=node_source_is_configured(PATHS)
        self.source_var.set(f"就绪（{detail}）" if ok else f"未配置（{detail}）")
        for warning in drain_config_warnings():
            self.log("[CONFIG] " + warning)
        self._refresh_subscription_info()

    def _refresh_subscription_info(self, force=False):
        src=load_node_source(PATHS)
        if str(src.get("mode","file")).lower()!="subscription":
            self.sub_var.set("本地节点模式（无订阅流量信息）"); return
        urls=[u for u in src.get("subscription_urls",[]) if u]
        if not urls:
            self.sub_var.set("-"); return
        if self._sub_fetching: return
        if not force and time.time()-self._sub_last<60: return
        self._sub_fetching=True
        self.sub_var.set("查询中...")
        def work():
            parts=[]
            try:
                for i,url in enumerate(urls,1):
                    try:
                        ok,info,reason=check_subscription(url)
                        tag=f"#{i} " if len(urls)>1 else ""
                        parts.append(tag+(describe_userinfo(info) if ok else f"订阅不可用（{reason}）"))
                    except Exception as e:
                        parts.append(f"#{i} 查询失败（{type(e).__name__}）")
                text=" ｜ ".join(parts) if parts else "订阅信息不可用"
            finally:
                self._sub_fetching=False
                self._sub_last=time.time()
            self._ui(lambda t=text:self.sub_var.set(t))
        threading.Thread(target=work,daemon=True).start()

    def _start_traffic_monitor(self):
        if self._traffic_stop: self._traffic_stop.set()
        self._traffic_stop=threading.Event()
        s=load_app_settings(PATHS)
        api=MihomoApi(int(s.get("controller_port",19090)), s.get("api_secret",""))
        threading.Thread(target=self._traffic_loop,args=(self._traffic_stop,api),daemon=True).start()

    def _traffic_loop(self,stop,api):
        last=None
        while not stop.wait(2):
            if not self.core.is_running(): continue
            try:
                data=api.connections()
                up=int(data.get("uploadTotal",0)); down=int(data.get("downloadTotal",0))
                now=time.time()
                if last:
                    dt=max(0.5, now-last[0])
                    text=(f"↑ {fmt_speed((up-last[1])/dt)}  ↓ {fmt_speed((down-last[2])/dt)}"
                          f" · 本次经内核 {fmt_bytes(up+down)}（含直连）")
                else:
                    text=f"本次经内核 {fmt_bytes(up+down)}（含直连）"
                last=(now,up,down)
                self._ui(lambda t=text:self.traffic_var.set(t))
            except Exception:
                last=None

    def latency_targets(self, selected):
        urls=[]
        for pid in selected:
            p=self.profile_by_id.get(pid, {})
            urls.extend(p.get("latency_test_urls",[]))
        return list(dict.fromkeys(u for u in urls if u))

    def make_selector(self, selected=None):
        s=load_app_settings(PATHS)
        return JapanNodeSelector(
            MihomoApi(int(s["controller_port"]), s["api_secret"]),
            self.log,
            keywords=s["jp_keywords"],
            required_urls=self.latency_targets(selected or self.selected_profiles()),
            timeout_ms=int(s["latency_timeout_ms"]),
            light_url=s["latency_test_url"],
            cancel_event=self._start_cancel,
            total_timeout_s=35
        )

    def _verify_core_worker(self):
        try:
            result = self.core.verify_binary_status()
        finally:
            self._core_verifying = False

        if result.state == VALID:
            self._ui(lambda: self.core_var.set("就绪"))
            return

        if result.state == TRANSIENT:
            self._core_verify_retry_after = time.time() + 3.0
            self.log(f"[CORE] 完整性校验暂时无法完成：{result.detail}")
            self._ui(lambda: self.core_var.set("暂时无法校验"))
            return

        self.log(f"[CORE] 内核需要修复：{result.detail}")
        self._ui(lambda: self.core_var.set("需要修复"))
        self._ui(self._ensure_core)

    def _ensure_core(self):
        if self._core_installing or self._core_verifying:
            return
        if self.core.is_running():
            self.log("[CORE] 内核正在运行，本次自动修复已跳过。")
            return
        cached = self.core.verification_detail()
        if cached and cached.state == VALID:
            self.core_var.set("就绪")
            return

        self._core_installing=True
        self.core_var.set("自动下载/修复中...")
        self.log("[CORE] 正在使用固定可信归档修复/安装 Mihomo...")
        def work():
            try:
                download_core(PATHS,self.log)
                self.core.invalidate_verify_cache()
            except Exception as e:
                self.log(f"[CORE] 自动修复/下载失败：{e}（不会删除现有可执行文件）")
            finally:
                self._core_installing=False
                self._ui(self.refresh_status)
        threading.Thread(target=work,daemon=True).start()

    def _autostart_when_core_ready(self):
        if self._closing:
            return
        if self.core.verified_state() is True:
            self.start_accel()
            return
        if self._autostart_deadline and time.time() >= self._autostart_deadline:
            self.log("[CORE] 管理员重启后的内核准备超过 30 秒，已取消自动启动；请查看内核状态后手动重试。")
            self.status_var.set("等待手动启动")
            return
        if (not self._core_installing and not self._core_verifying and
                time.time() >= self._core_verify_retry_after):
            self.refresh_status()
        self.root.after(500, self._autostart_when_core_ready)

    def open_settings(self):
        SettingsWindow(self.root,self.profiles,self.refresh_status)

    def open_custom_sites(self):
        CustomSitesWindow(self.root, self._custom_saved)

    def _custom_saved(self, select_id=None):
        self.reload_profiles(first=False)
        if select_id and select_id in self.profile_vars:
            self.profile_vars[select_id].set(True)
            self.log(f"[CUSTOM] 新增配置 {select_id} 已自动勾选；重新点“一键加速”即可生效。")
        else:
            self.log("[CUSTOM] 本地自定义配置已重新加载。")

    def start_accel(self):
        selected=self.selected_profiles()
        effective=list(selected)
        if self.services_var.get():
            effective+=[p["id"] for p in self.always_on if p["id"] not in effective]
        if not effective:
            messagebox.showwarning("FLY","请至少勾选一个分流配置（或开启常用服务默认加速）。"); return
        empty=[self.profile_by_id[p]["name"] for p in effective if not profile_has_effect(self.profile_by_id[p])]
        if empty:
            messagebox.showerror("FLY","以下配置没有任何有效分流规则，已拒绝启动：\n"+"、".join(empty)); return
        if self._core_verifying:
            messagebox.showinfo("FLY","正在校验加速内核，请稍后再点一键加速。"); return
        if self._core_installing:
            messagebox.showinfo("FLY","正在修复/安装加速内核，请完成后再点一键加速。"); return
        if self.core.verified_state() is not True:
            self.refresh_status()
            messagebox.showinfo("FLY","加速内核尚未通过完整性校验；FLY 不会在验证前执行它。"); return
        ok,_=node_source_is_configured(PATHS)
        if not ok:
            messagebox.showwarning("FLY","请先在设置里配置你自己的节点/订阅。"); return
        tun_names=[self.profile_by_id[p]["name"] for p in effective if _needs_tun(self.profile_by_id[p])]
        if tun_names and not is_admin():
            if messagebox.askyesno("FLY",f"{'、'.join(tun_names)} 需要 TUN 模式（管理员权限），是否以管理员身份重启？") \
               and relaunch_as_admin(",".join(selected), autostart=True):
                # Only tear down after UAC launch succeeded; the elevated copy
                # waits on our mutex while we restore proxy/core cleanly.
                self._teardown()
                self.root.after(300,self.on_close)
            return
        # Reapplying settings must fully tear down the previous routing state
        # before the new core starts; otherwise old system proxy can feed a
        # not-yet-selected FLY-JP group.
        if self.core.is_running() or self.sysproxy_active:
            self._teardown()
        self._start_cancel = threading.Event()
        self.status_var.set("启动中..."); self.start_btn.configure(state="disabled")
        self._start_token += 1
        self._start_thread = threading.Thread(
            target=self._start_worker, args=(effective, selected, self._start_token), daemon=True)
        self._start_thread.start()

    def _check_cancelled(self, token):
        if self._closing or token != self._start_token or self._start_cancel.is_set():
            raise _Cancelled()

    def _start_worker(self,effective,selected,token):
        try:
            names="、".join(self.profile_by_id[p]["name"] for p in selected) or "（无）"
            self.log(f"[FLY] 已选配置：{names}")
            if len(effective)>len(selected):
                svc="、".join(self.profile_by_id[p]["name"] for p in effective if p not in selected)
                self.log(f"[FLY] 默认加速的常用服务：{svc}")
            self._check_cancelled(token)
            self._ui(lambda:self.status_var.set("启动内核..."))
            self.core.start(effective)
            self._check_cancelled(token)
            self._ui(lambda:self.status_var.set("检测日本节点..."))
            self.selector=self.make_selector(selected)
            s=load_app_settings(PATHS)
            chosen,delay=self.selector.auto_select(
                "FLY-JP",wait_s=20,
                preferred=str(s.get("last_node","")).strip() or None,
                sticky_max_delay_ms=int(s.get("sticky_max_delay_ms",1000))
            )
            self._check_cancelled(token)
            # Only a verified healthy node is persisted.
            self._remember_node(chosen)
            self._ui(lambda n=chosen:self.node_var.set(n))
            self._ui(lambda d=delay:self.delay_var.set(f"{d} ms" if d is not None else "unknown"))

            uses_tun=any(_needs_tun(self.profile_by_id[p]) for p in effective)
            has_domain_only=any(not _needs_tun(self.profile_by_id[p]) for p in effective)
            if has_domain_only and not uses_tun:
                port=int(s["mixed_port"])
                with self._proxy_lock:
                    self._check_cancelled(token)
                    self.sysproxy.enable(port); self.sysproxy_active=True
            self._check_cancelled(token)
            log_hints(PATHS,selected,self.log,profiles=self.profile_by_id)
            self._start_watchdog()
            self._start_traffic_monitor()
            self._refresh_subscription_info(force=True)
            self._ui(lambda:self.status_var.set(f"加速中（{len(effective)} 项）"))
        except _Cancelled:
            self.log("[FLY] 启动已取消，正在收拾现场...")
            self._teardown()
            self._ui(lambda:self.status_var.set("已停止"))
        except Exception as e:
            self.log(f"[ERROR] {e}")
            self._teardown()
            self._ui(lambda:self.status_var.set("已停止"))
            self._ui(lambda e=e:messagebox.showerror("FLY",str(e)))
        finally:
            self._ui(lambda:self.start_btn.configure(state="normal"))

    def switch_node(self):
        if not self.core.is_running():
            messagebox.showinfo("FLY","请先启动加速。"); return
        if not self._selection_lock.acquire(blocking=False):
            messagebox.showinfo("FLY","正在进行节点检测/切换，请稍后再试。"); return
        self.switch_btn.configure(state="disabled")
        def work():
            try:
                sel=self.selector or self.make_selector()
                n,d=sel.cycle_next("FLY-JP"); self.selector=sel
                self._remember_node(n)
                self._ui(lambda:self.node_var.set(n))
                self._ui(lambda:self.delay_var.set(f"{d} ms" if d is not None else "unknown"))
            except Exception as e:
                self._ui(lambda e=e:messagebox.showerror("FLY",str(e)))
            finally:
                self._selection_lock.release()
                self._ui(lambda:self.switch_btn.configure(state="normal"))
        threading.Thread(target=work,daemon=True).start()

    def _remember_node(self,name):
        try:
            update_app_settings(PATHS, {"last_node": str(name)})
        except Exception:
            pass

    def _on_core_line(self, line):
        """内核日志钩子（跑在读日志线程上，只做最轻量的判断）。"""
        if self._dial_tracker.feed(line):
            self.log("[WATCH] 内核连续报告出站失败，当前节点可能已不可用，立即重新检测...")
            self._probe_now.set()

    def _start_watchdog(self):
        if self._watch_stop:
            self._watch_stop.set(); self._probe_now.set()
        self._watch_stop=threading.Event()
        self._probe_now.clear(); self._dial_tracker.reset()
        threading.Thread(target=self._watchdog_loop,args=(self._watch_stop,),daemon=True).start()

    def _watchdog_loop(self,stop):
        fails=0
        while not stop.is_set():
            woken=self._probe_now.wait(60)
            if stop.is_set(): return
            self._probe_now.clear()
            sel=self.selector
            if not sel or not self.core.is_running(): continue
            try:
                current=sel.api.get_group("FLY-JP").get("now")
                if not current: continue
                if woken:
                    # Real outbound dial failures outrank a provider healthcheck
                    # that may be stale/false-positive. Exclude the bad node now.
                    self.log("[WATCH] 真实出站连续失败，直接排除当前节点并切换...")
                    with self._selection_lock:
                        chosen,delay=sel.auto_select("FLY-JP",wait_s=10,exclude={current})
                    fails=0
                else:
                    sel.measure_light(current)
                    fails=0
                    continue
                self._remember_node(chosen)
                self._ui(lambda n=chosen:self.node_var.set(n))
                self._ui(lambda d=delay:self.delay_var.set(f"{d} ms" if d is not None else "unknown"))
            except Exception as e:
                if woken:
                    self.log(f"[WATCH] 无健康备用日本节点：{e}；为避免浏览器挂在死代理上，停止加速并恢复直连。")
                    self._teardown()
                    self._ui(lambda:self.status_var.set("节点不可用（已停止）"))
                    self._ui(lambda:self.node_var.set("-"))
                    self._ui(lambda:self.delay_var.set("-"))
                    return
                fails+=1
                self.log(f"[WATCH] 所选服务健康检查失败（{fails}/3）：{e}")
                if fails>=3:
                    fails=0
                    try:
                        with self._selection_lock:
                            chosen,delay=sel.auto_select("FLY-JP",wait_s=10,exclude={current})
                        self._remember_node(chosen)
                        self._ui(lambda n=chosen:self.node_var.set(n))
                        self._ui(lambda d=delay:self.delay_var.set(f"{d} ms" if d is not None else "unknown"))
                    except Exception as reselect_error:
                        self.log(f"[WATCH] Reselect failed: {reselect_error}")

    def _ui(self, fn):
        """把 UI 更新投递回主线程；窗口已销毁时安静丢弃。"""
        try:
            self.root.after(0, fn)
        except (RuntimeError, tk.TclError):
            pass

    def _on_core_exit(self, code):
        """内核意外退出：立刻还原系统代理，并让界面如实反映状态。

        不这么做的话，系统代理会一直指向一个没人监听的端口——
        浏览器全网打不开，而界面还显示“加速中”。"""
        self._start_token += 1        # 让还在跑的启动流程立刻作废
        if self._watch_stop:
            self._watch_stop.set(); self._watch_stop = None
            self._probe_now.set()   # 叫醒看门狗，让它立刻看到 stop 并退出
        if self._traffic_stop:
            self._traffic_stop.set(); self._traffic_stop = None
        with self._proxy_lock:
            if self.sysproxy_active:
                self.sysproxy.restore(); self.sysproxy_active = False
                self.log("[CORE] 已自动还原系统代理，浏览器恢复直连。")
        self.selector = None
        self._ui(lambda: self.status_var.set("内核异常退出"))
        self._ui(lambda: self.node_var.set("-"))
        self._ui(lambda: self.delay_var.set("-"))
        self._ui(lambda: self.traffic_var.set("-"))
        self._ui(lambda: self.start_btn.configure(state="normal"))
        self._ui(lambda: messagebox.showwarning(
            "FLY", "加速内核意外退出，已自动还原系统代理。\n详情见日志，可重新点“一键加速”。"))

    def _teardown(self):
        self._start_cancel.set()
        # 令牌一变，还在跑的启动流程下一个检查点就会退出
        self._start_token += 1
        if self._watch_stop:
            self._watch_stop.set(); self._watch_stop=None
            self._probe_now.set()   # 叫醒看门狗，让它立刻看到 stop 并退出
        if self._traffic_stop:
            self._traffic_stop.set(); self._traffic_stop=None
        self._ui(lambda:self.traffic_var.set("-"))
        with self._proxy_lock:
            if self.sysproxy_active:
                self.sysproxy.restore(); self.sysproxy_active=False
        self.core.stop(); self.selector=None

    def stop_accel(self):
        self._teardown()
        self.node_var.set("-"); self.delay_var.set("-"); self.status_var.set("已停止")
        self.log("[FLY] 加速已停止。")

    def on_close(self):
        self._closing = True
        self._start_token += 1
        self._start_cancel.set()
        self._probe_now.set()
        t = self._start_thread
        if t and t.is_alive():
            self.log("[FLY] 等待启动流程退出...")
            t.join(timeout=5)
        self._teardown()
        try:
            if self._log_file:
                with self._log_lock:
                    self._log_file.flush(); self._log_file.close(); self._log_file=None
        except OSError:
            pass
        self.root.destroy()

class SettingsWindow(tk.Toplevel):
    def __init__(self,master,profiles,on_saved):
        super().__init__(master)
        self.title("FLY - 节点 / 应用设置"); self.geometry("720x660")
        ensure_private_files(PATHS)
        src=load_node_source(PATHS); app=load_app_settings(PATHS)
        self.on_saved=on_saved
        self.mode=tk.StringVar(value=src.get("mode","file"))
        self._sub_urls=list(src.get("subscription_urls",[]))
        self.timeout=tk.StringVar(value=str(app.get("latency_timeout_ms",5000)))
        self.sticky=tk.StringVar(value=str(app.get("sticky_max_delay_ms",1000)))
        self.tun_profiles=[r for r in profiles if _needs_tun(r)]
        exes=app.get("game_exes",{})
        self.exe_vars={r["id"]:tk.StringVar(value=str(exes.get(r["id"],""))) for r in self.tun_profiles}

        f=ttk.Frame(self,padding=14); f.pack(fill="both",expand=True)
        box=ttk.LabelFrame(f,text="你的节点来源",padding=10); box.pack(fill="x")
        ttk.Radiobutton(box,text="本地 private\\nodes.yaml",variable=self.mode,value="file").pack(anchor="w")
        ttk.Radiobutton(box,text="Clash/Mihomo 订阅 URL（可填多条，每行一个；节点合并使用，一家挂了自动用另一家）",
                        variable=self.mode,value="subscription").pack(anchor="w",pady=(8,0))
        self.sub_text=tk.Text(box,height=3,font=("Consolas",9))
        self.sub_text.pack(fill="x",pady=(7,0))
        self.sub_text.insert("1.0","\n".join(self._sub_urls))
        ttk.Button(box,text="打开 nodes.yaml",command=lambda:os.startfile(str(PATHS.nodes_yaml))).pack(anchor="w",pady=(10,0))

        auto=ttk.LabelFrame(f,text="日本节点自动选择",padding=10); auto.pack(fill="x",pady=(12,0))
        ttk.Label(auto,text="只筛选名称含 Japan / JPN / JP / 日本 / Tokyo / Osaka / 🇯🇵 的节点；选线与保活都会验证所选配置的服务目标，避免“Google 能通但游戏站点被挡”的假健康。",wraplength=650).pack(anchor="w")
        rr=ttk.Frame(auto); rr.pack(fill="x",pady=(10,0))
        ttk.Label(rr,text="超时 (ms)").pack(side="left"); ttk.Entry(rr,textvariable=self.timeout,width=10).pack(side="left",padx=(8,18))
        ttk.Label(rr,text="延迟不超过").pack(side="left"); ttk.Entry(rr,textvariable=self.sticky,width=10).pack(side="left",padx=(8,4)); ttk.Label(rr,text="ms 时沿用上次节点").pack(side="left")

        if self.tun_profiles:
            nk=ttk.LabelFrame(f,text="TUN 配置的可执行文件（可选，仅用于按进程名分流，不会自动启动游戏）",padding=10)
            nk.pack(fill="x",pady=(12,0))
            for r in self.tun_profiles:
                rr=ttk.Frame(nk); rr.pack(fill="x",pady=(0,6))
                ttk.Label(rr,text=r["name"],width=22).pack(side="left")
                ttk.Entry(rr,textvariable=self.exe_vars[r["id"]]).pack(side="left",fill="x",expand=True)
                ttk.Button(rr,text="浏览...",command=lambda pid=r["id"]:self.browse(pid)).pack(side="left",padx=(8,0))

        ttk.Label(f,text="订阅 URL、UUID 等凭据只保存在本机 private\\ 目录，已被 Git 排除。FLY 不提供节点、账号、会员或 VPS 服务。",
                  wraplength=670).pack(anchor="w",pady=(14,0))
        ttk.Button(f,text="保存",command=self.save).pack(anchor="e",pady=(14,0))

    def browse(self,pid):
        p=filedialog.askopenfilename(filetypes=[("Windows executable","*.exe"),("All files","*.*")])
        if p:self.exe_vars[pid].set(p)

    def save(self):
        try:
            timeout=int(self.timeout.get().strip()); sticky=int(self.sticky.get().strip())
            if timeout<1000 or timeout>30000 or sticky<100: raise ValueError
        except ValueError:
            messagebox.showerror("FLY","请输入有效数值：超时 1000–30000ms，沿用阈值 ≥100ms。"); return
        urls=[u.strip() for u in self.sub_text.get("1.0","end").splitlines() if u.strip()]
        save_json(PATHS.node_source,{"mode":self.mode.get(),
                                     "subscription_urls":urls,
                                     "subscription_url":urls[0] if urls else ""})
        values={}
        for pid,var in self.exe_vars.items():
            value=var.get().strip()
            name=Path(value).name if value else ""
            if any(ch in name for ch in (",","#","\n","\r")):
                messagebox.showerror("FLY",f"可执行文件名含分流规则不允许的字符：{name}"); return
            values[pid]=value
        def mutate(app):
            exes=dict(app.get("game_exes",{})); exes.update(values)
            app["game_exes"]=exes
            app["latency_timeout_ms"]=timeout
            app["sticky_max_delay_ms"]=sticky
        update_app_settings(PATHS, mutator=mutate)
        self.on_saved(); messagebox.showinfo("FLY","设置已保存。"); self.destroy()

class CustomSitesWindow(tk.Toplevel):
    def __init__(self,master,on_saved):
        super().__init__(master)
        self.title("FLY - 添加/管理加速网站"); self.geometry("640x520")
        self.on_saved=on_saved

        f=ttk.Frame(self,padding=14); f.pack(fill="both",expand=True)
        ttk.Label(f,text="添加要加速的网站",font=("Segoe UI",13,"bold")).pack(anchor="w")
        ttk.Label(f,text="粘贴网址（或直接填域名），自动生成分流配置并出现在主界面列表；只保存在本机，不会上传。",
                  wraplength=600).pack(anchor="w",pady=(4,8))

        form=ttk.Frame(f); form.pack(fill="x")
        ttk.Label(form,text="网址").pack(side="left")
        self.url_var=tk.StringVar()
        ttk.Entry(form,textvariable=self.url_var).pack(side="left",fill="x",expand=True,padx=(8,12))
        ttk.Label(form,text="名称(可选)").pack(side="left")
        self.name_var=tk.StringVar()
        ttk.Entry(form,textvariable=self.name_var,width=16).pack(side="left",padx=(8,12))
        ttk.Button(form,text="添加",command=self.add_site).pack(side="left")

        ttk.Label(f,text="我的加速网站：",font=("Segoe UI",10,"bold")).pack(anchor="w",pady=(14,4))
        self.listf=ttk.Frame(f); self.listf.pack(fill="both",expand=True)
        self.refresh_list()

        bottom=ttk.Frame(f); bottom.pack(fill="x",pady=(10,0))
        ttk.Label(bottom,text="站点由第三方运营，请遵守所在地法律与站点条款。",foreground="#888").pack(side="left")
        ttk.Button(bottom,text="高级编辑 (JSON)...",command=self.open_json_editor).pack(side="right")

    def refresh_list(self):
        for child in self.listf.winfo_children():
            child.destroy()
        self.customs=load_custom_profiles(PATHS)
        if not self.customs:
            ttk.Label(self.listf,text="（还没有添加过网站）",foreground="#888").pack(anchor="w")
        for p in self.customs:
            row=ttk.Frame(self.listf); row.pack(fill="x",pady=2)
            ttk.Label(row,text=p["name"],width=24).pack(side="left")
            ttk.Label(row,text=" / ".join(p.get("domains",[])[:3]),foreground="#888").pack(side="left",padx=(6,0))
            ttk.Button(row,text="删除",width=6,
                       command=lambda pid=p["id"],name=p["name"]:self.remove_site(pid,name)).pack(side="right")

    def add_site(self):
        try:
            profile=profile_from_url(self.url_var.get(),self.name_var.get())
        except Exception as e:
            messagebox.showerror("FLY",str(e),parent=self); return
        existing=load_custom_profiles(PATHS)
        taken={p["id"] for p in list_routing_profiles(PATHS)}
        pid=profile["id"]; n=2
        while profile["id"] in taken:
            profile["id"]=f"{pid}-{n}"; n+=1
        cleaned=[{k:v for k,v in p.items() if k not in ("source","invalid_values")} for p in existing]
        cleaned.append(profile)
        save_custom_profiles(PATHS,cleaned)
        self.url_var.set(""); self.name_var.set("")
        self.refresh_list(); self.on_saved(profile["id"])

    def remove_site(self,pid,name):
        if not messagebox.askyesno("FLY",f"删除「{name}」？",parent=self): return
        cleaned=[{k:v for k,v in p.items() if k not in ("source","invalid_values")}
                 for p in load_custom_profiles(PATHS) if p["id"]!=pid]
        save_custom_profiles(PATHS,cleaned)
        self.refresh_list(); self.on_saved()

    def open_json_editor(self):
        CustomProfilesWindow(self,self._json_saved)

    def _json_saved(self):
        self.refresh_list(); self.on_saved()

class CustomProfilesWindow(tk.Toplevel):
    TEMPLATE = {
        "profiles": [{
            "id": "my-jp-service",
            "name": "My JP Service",
            "category": "Custom",
            "launch_mode": "browser",
            "domains": ["example.jp"],
            "keywords": [],
            "ip_cidrs": [],
            "processes": [],
            "ports": [],
            "latency_test_urls": ["https://example.jp/"]
        }]
    }
    def __init__(self,master,on_saved):
        super().__init__(master)
        self.title("FLY - 本地自定义配置"); self.geometry("780x650")
        self.on_saved=on_saved
        f=ttk.Frame(self,padding=12); f.pack(fill="both",expand=True)
        ttk.Label(f,text="本地 JSON 配置编辑器",font=("Segoe UI",13,"bold")).pack(anchor="w")
        ttk.Label(f,text="内容保存在 private\\custom_profiles.json，永远不会被提交。domains / processes / ip_cidrs / ports 只写你明确要代理的流量；注意 ports 是全系统级按端口匹配（如填 443 等于全部 HTTPS），请谨慎使用。",wraplength=740).pack(anchor="w",pady=(4,8))
        self.text=tk.Text(f,wrap="none",font=("Consolas",10))
        self.text.pack(fill="both",expand=True)
        current={"profiles":[{k:v for k,v in p.items() if k not in ("source","invalid_values")} for p in load_custom_profiles(PATHS)]}
        if not current["profiles"]: current=self.TEMPLATE
        self.text.insert("1.0",json.dumps(current,ensure_ascii=False,indent=2))
        row=ttk.Frame(f); row.pack(fill="x",pady=(8,0))
        ttk.Button(row,text="保存本地配置",command=self.save).pack(side="right")

    def save(self):
        try:
            data=json.loads(self.text.get("1.0","end"))
            if not isinstance(data,dict) or not isinstance(data.get("profiles"),list):
                raise ValueError("顶层必须包含 profiles 数组。")
            ids=set(); problems=[]
            reserved={p["id"] for p in list_routing_profiles(PATHS) if p.get("source")=="builtin"}
            for p in data["profiles"]:
                if not isinstance(p,dict): raise ValueError("每个配置必须是对象。")
                pid=str(p.get("id","")).strip(); name=str(p.get("name","")).strip()
                if not pid or not name: raise ValueError("每个配置都需要 id 和 name。")
                if pid in reserved: raise ValueError(f"配置 id 与内置配置冲突：{pid}")
                if pid in ids: raise ValueError(f"配置 id 重复：{pid}")
                if "sort" in p:
                    try: int(p["sort"])
                    except (TypeError, ValueError):
                        raise ValueError(f"配置 {pid} 的 sort 必须是整数。")
                if not profile_has_effect(p):
                    raise ValueError(f"配置 {pid} 不含任何 domains/keywords/ip_cidrs/ports/processes/full_browser 规则。")
                ids.add(pid)
                problems += [f"{pid}: {x}" for x in validate_profile(p)]
            if problems:
                raise ValueError("以下字段不合法：\n" + "\n".join(problems[:10]))
            save_custom_profiles(PATHS,data["profiles"])
        except Exception as e:
            messagebox.showerror("FLY",f"配置 JSON 无效：\n{e}"); return
        self.on_saved(); messagebox.showinfo("FLY","自定义配置已保存到本机。"); self.destroy()

def main():
    p=argparse.ArgumentParser(add_help=False)
    p.add_argument("--games",default="")
    p.add_argument("--game",default="")
    p.add_argument("--autostart",action="store_true")
    p.add_argument("--takeover",action="store_true")
    args,_=p.parse_known_args()

    guard=SingleInstance()
    if not guard.acquire(wait_ms=10000 if args.takeover else 0):
        try:
            r=tk.Tk(); r.withdraw()
            messagebox.showwarning("FLY","FLY 已经在运行。请使用现有窗口，避免两个实例互相修改系统代理。")
            r.destroy()
        except Exception:
            pass
        return
    try:
        ensure_private_files(PATHS)
        initial=[g.strip() for g in f"{args.games},{args.game}".split(",") if g.strip()]
        root=tk.Tk()
        FlyApp(root,initial,args.autostart)
        root.mainloop()
    finally:
        guard.close()

if __name__=="__main__":
    try:
        main()
    except Exception:
        import traceback
        err=traceback.format_exc()
        try:
            PATHS.runtime.mkdir(parents=True,exist_ok=True)
            (PATHS.runtime/"error.log").write_text(err,encoding="utf-8")
        except OSError: pass
        try:
            r=tk.Tk(); r.withdraw()
            messagebox.showerror("FLY",f"启动失败（详情见 runtime\\error.log）：\n\n{err[-1500:]}")
        except Exception: pass
