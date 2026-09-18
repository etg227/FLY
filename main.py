from __future__ import annotations
import argparse, os, queue, subprocess, threading, time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from backend.config import (Paths, ensure_private_files, list_game_rules, load_app_settings,
                            load_node_source, node_source_is_configured, save_json)
from backend.core_installer import install_core as download_core
from backend.core_manager import CoreManager
from backend.launcher import log_hints
from backend.mihomo_api import MihomoApi, JapanNodeSelector
from backend.system_proxy import SystemProxy
from backend.windows_admin import is_admin, relaunch_as_admin

APP_DIR = Path(__file__).resolve().parent
PATHS = Paths(APP_DIR)

class FlyApp:
    def __init__(self, root, initial_games=None, autostart=False):
        ensure_private_files(PATHS)
        self.root = root
        self.root.title("FLY v0.4 - Selective Game Accelerator")
        self.root.geometry("840x690")
        self.root.minsize(780, 620)

        self.logs = queue.Queue()
        self.core = CoreManager(PATHS, self.log)
        self.sysproxy = SystemProxy(PATHS.runtime / "sysproxy_backup.json", self.log)
        self.selector = None
        self.sysproxy_active = False
        self._watch_stop = None
        self._core_installing = False

        self.rules = list_game_rules(PATHS)
        self.rule_by_id = {r["id"]: r for r in self.rules}
        initial = {g for g in (initial_games or []) if g in self.rule_by_id}
        if not initial and self.rules:
            initial = {self.rules[0]["id"]}
        self.game_vars = {r["id"]: tk.BooleanVar(value=r["id"] in initial) for r in self.rules}

        self.status_var = tk.StringVar(value="Stopped")
        self.core_var = tk.StringVar()
        self.source_var = tk.StringVar()
        self.node_var = tk.StringVar(value="-")
        self.delay_var = tk.StringVar(value="-")

        self.build()
        self.refresh_status()
        self.root.after(100, self.flush_logs)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.log("Ready.")
        self.log("v0.4: check one or MORE games, then accelerate; system proxy is set automatically and restored on stop.")
        self.log("Non-game traffic remains DIRECT.")
        self.sysproxy.restore_orphan()
        if autostart:
            self.log("[FLY] Autostart requested (admin relaunch).")
            self.root.after(600, self.start_accel)

    def build(self):
        outer = ttk.Frame(self.root, padding=16); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="FLY v0.4", font=("Segoe UI",21,"bold")).pack(anchor="w")
        ttk.Label(outer, text="勾选游戏（可多选）→ 自动筛日本节点 → 自动测速 → 一键加速").pack(anchor="w", pady=(0,14))

        back = ttk.LabelFrame(outer, text="Backend", padding=10); back.pack(fill="x", pady=(0,10))
        for i, (label, var) in enumerate([("Core:",self.core_var),("Node source:",self.source_var),("Japan node:",self.node_var)]):
            row = ttk.Frame(back); row.pack(fill="x", pady=(0 if i==0 else 8,0))
            ttk.Label(row,text=label,width=14).pack(side="left")
            ttk.Label(row,textvariable=var,font=("Segoe UI",10,"bold") if i==2 else None).pack(side="left")
            if i==1:
                ttk.Button(row,text="Node / App Settings",command=self.open_settings).pack(side="right")
            elif i==2:
                ttk.Label(row,text="Latency:").pack(side="left", padx=(18,4))
                ttk.Label(row,textvariable=self.delay_var).pack(side="left")

        games = ttk.LabelFrame(outer,text="Games（可多选，同时加速）",padding=12); games.pack(fill="x", pady=(0,10))
        per_row = 3
        for i, rule in enumerate(self.rules):
            mode = str(rule.get("launch_mode","browser")).lower()
            label = rule["name"] + ("  [TUN]" if mode == "tun" else "")
            cb = ttk.Checkbutton(games, text=label, variable=self.game_vars[rule["id"]])
            cb.grid(row=i//per_row, column=i%per_row, sticky="w", padx=(0,24), pady=2)

        buttons = ttk.Frame(outer); buttons.pack(fill="x",pady=(2,12))
        self.start_btn = ttk.Button(buttons,text="一键加速",command=self.start_accel); self.start_btn.pack(side="left")
        ttk.Button(buttons,text="停止",command=self.stop_accel).pack(side="left",padx=8)
        ttk.Button(buttons,text="换日本节点",command=self.switch_node).pack(side="left",padx=8)
        ttk.Button(buttons,text="刷新状态",command=self.refresh_status).pack(side="left",padx=8)
        ttk.Label(buttons,text="Status:").pack(side="right")
        ttk.Label(buttons,textvariable=self.status_var,font=("Segoe UI",10,"bold")).pack(side="right",padx=(0,5))

        route = ttk.LabelFrame(outer,text="Routing",padding=10); route.pack(fill="x",pady=(0,10))
        ttk.Label(route,justify="left",text=(
            "浏览器游戏：自动设置系统代理，用你自己的浏览器直接玩；仅勾选游戏的域名 → Japan node，其余 DIRECT。\n"
            "TUN 游戏：需管理员权限，按进程/域名分流；其他程序不受影响。\n"
            "停止或退出时自动还原系统代理。若 DMM 不接受当前出口，可点“换日本节点”。"
        )).pack(anchor="w")

        logf = ttk.LabelFrame(outer,text="Live log",padding=8); logf.pack(fill="both",expand=True)
        self.logbox = tk.Text(logf,wrap="word",font=("Consolas",9),height=16); self.logbox.pack(side="left",fill="both",expand=True)
        sb=ttk.Scrollbar(logf,orient="vertical",command=self.logbox.yview); sb.pack(side="right",fill="y"); self.logbox.configure(yscrollcommand=sb.set)

    def log(self,msg): self.logs.put(f"{time.strftime('%H:%M:%S')} {msg}")
    def flush_logs(self):
        try:
            while True:
                x=self.logs.get_nowait(); self.logbox.insert("end",x+"\n"); self.logbox.see("end")
        except queue.Empty: pass
        self.root.after(100,self.flush_logs)

    def refresh_status(self):
        if PATHS.core_exe.exists():
            self.core_var.set("Ready")
        elif self._core_installing:
            self.core_var.set("Downloading...")
        else:
            self.core_var.set("Missing")
            self._ensure_core()
        ok,detail=node_source_is_configured(PATHS)
        self.source_var.set(f"Ready ({detail})" if ok else f"Not configured ({detail})")

    def selected_games(self):
        return [r["id"] for r in self.rules if self.game_vars[r["id"]].get()]

    def make_selector(self):
        s=load_app_settings(PATHS)
        return JapanNodeSelector(
            MihomoApi(int(s.get("controller_port",19090)), s.get("api_secret","")),
            self.log,
            keywords=s.get("jp_keywords"),
            test_url=s.get("latency_test_url","https://www.gstatic.com/generate_204"),
            timeout_ms=int(s.get("latency_timeout_ms",5000))
        )

    def _ensure_core(self):
        # Fallback for when the launcher's core download did not finish; the
        # user never installs the core manually.
        if PATHS.core_exe.exists() or self._core_installing: return
        self._core_installing=True
        self.refresh_status()
        self.log("[CORE] 未检测到加速内核，自动下载中...")
        def work():
            try:
                download_core(PATHS,self.log)
            except Exception as e:
                self.log(f"[CORE] 自动下载失败：{e}（点“刷新状态”或重启程序会自动重试）")
            finally:
                self._core_installing=False
                self.root.after(0,self.refresh_status)
        threading.Thread(target=work,daemon=True).start()

    def open_settings(self): SettingsWindow(self.root,self.rules,self.refresh_status)

    def start_accel(self):
        selected=self.selected_games()
        if not selected:
            messagebox.showwarning("FLY","请至少勾选一个游戏。"); return
        if not PATHS.core_exe.exists():
            self._ensure_core()
            messagebox.showinfo("FLY","加速内核正在自动下载（进度见日志），完成后再点一键加速。"); return
        ok,_=node_source_is_configured(PATHS)
        if not ok:
            messagebox.showwarning("FLY","节点/订阅尚未配置。"); return
        tun_names=[self.rule_by_id[g]["name"] for g in selected
                   if str(self.rule_by_id[g].get("launch_mode","browser")).lower()=="tun"]
        if tun_names and not is_admin():
            if messagebox.askyesno("FLY",f"{'、'.join(tun_names)} 的 TUN 模式需要管理员权限，是否重启？") \
               and relaunch_as_admin(",".join(selected), autostart=True):
                self.root.after(300,self.root.destroy)
            return
        self.status_var.set("Starting..."); self.start_btn.configure(state="disabled")
        threading.Thread(target=self._start_worker,args=(selected,),daemon=True).start()

    def _start_worker(self,selected):
        try:
            names=", ".join(self.rule_by_id[g]["name"] for g in selected)
            self.log(f"[FLY] Selected game(s): {names}")
            self.core.start(selected)
            self.selector=self.make_selector()
            s=load_app_settings(PATHS)
            chosen,delay=self.selector.auto_select("FLY-JP",wait_s=45,
                preferred=str(s.get("last_node","")).strip() or None,
                sticky_max_delay_ms=int(s.get("sticky_max_delay_ms",1000)))
            self._remember_node(chosen)

            self.root.after(0,lambda n=chosen:self.node_var.set(n))
            self.root.after(0,lambda d=delay:self.delay_var.set(f"{d} ms" if d is not None else "unknown"))

            has_browser_game=any(str(self.rule_by_id[g].get("launch_mode","browser")).lower()=="browser"
                                 for g in selected)
            if has_browser_game:
                port=int(load_app_settings(PATHS).get("mixed_port",17890))
                self.sysproxy.enable(port); self.sysproxy_active=True
            log_hints(PATHS,selected,self.log)
            self._start_watchdog()
            self.root.after(0,lambda:self.status_var.set(f"Accelerating ({len(selected)})"))
        except Exception as e:
            self.log(f"[ERROR] {e}")
            self._teardown()
            self.root.after(0,lambda:self.status_var.set("Stopped"))
            self.root.after(0,lambda:messagebox.showerror("FLY",str(e)))
        finally:
            self.root.after(0,lambda:self.start_btn.configure(state="normal"))

    def switch_node(self):
        if not self.core.is_running():
            messagebox.showinfo("FLY","请先启动加速。"); return
        def work():
            try:
                sel=self.selector or self.make_selector()
                n,d=sel.cycle_next("FLY-JP"); self.selector=sel
                self._remember_node(n)
                self.root.after(0,lambda:self.node_var.set(n))
                self.root.after(0,lambda:self.delay_var.set(f"{d} ms" if d is not None else "unknown"))
            except Exception as e:
                self.root.after(0,lambda:messagebox.showerror("FLY",str(e)))
        threading.Thread(target=work,daemon=True).start()

    def _remember_node(self,name):
        try:
            s=load_app_settings(PATHS)
            if s.get("last_node")!=name:
                s["last_node"]=name; save_json(PATHS.app_settings,s)
        except Exception: pass

    def _start_watchdog(self):
        # Quietly re-checks the chosen node once a minute; only switches when
        # it is truly dead (3 straight failures), so the exit IP stays stable.
        if self._watch_stop: self._watch_stop.set()
        self._watch_stop=threading.Event()
        threading.Thread(target=self._watchdog_loop,args=(self._watch_stop,),daemon=True).start()

    def _watchdog_loop(self,stop):
        fails=0
        while not stop.wait(60):
            sel=self.selector
            if not sel or not self.core.is_running(): continue
            try:
                current=sel.api.get_group("FLY-JP").get("now")
                if not current: continue
                sel.api.delay(current,sel.test_url,sel.timeout_ms)
                fails=0
            except Exception:
                fails+=1
                self.log(f"[WATCH] Node check failed ({fails}/3).")
                if fails>=3:
                    fails=0
                    self.log("[WATCH] Current node looks dead; reselecting the fastest Japan node...")
                    try:
                        chosen,delay=sel.auto_select("FLY-JP",wait_s=10)
                        self._remember_node(chosen)
                        self.root.after(0,lambda n=chosen:self.node_var.set(n))
                        self.root.after(0,lambda d=delay:self.delay_var.set(f"{d} ms" if d is not None else "unknown"))
                    except Exception as e:
                        self.log(f"[WATCH] Reselect failed: {e}")

    def _teardown(self):
        if self._watch_stop:
            self._watch_stop.set(); self._watch_stop=None
        # Restore the proxy BEFORE stopping the core so the browser never
        # points at a dead port.
        if self.sysproxy_active:
            self.sysproxy.restore(); self.sysproxy_active=False
        self.core.stop(); self.selector=None

    def stop_accel(self):
        self._teardown()
        self.node_var.set("-"); self.delay_var.set("-"); self.status_var.set("Stopped")
        self.log("[FLY] Acceleration stopped.")

    def on_close(self):
        self._teardown(); self.root.destroy()

class SettingsWindow(tk.Toplevel):
    def __init__(self,master,rules,on_saved):
        super().__init__(master)
        self.title("FLY - Node / App Settings"); self.geometry("700x580")
        ensure_private_files(PATHS)
        src=load_node_source(PATHS); app=load_app_settings(PATHS)
        self.on_saved=on_saved
        self.mode=tk.StringVar(value=src.get("mode","file"))
        self.url=tk.StringVar(value=src.get("subscription_url",""))
        self.timeout=tk.StringVar(value=str(app.get("latency_timeout_ms",5000)))
        self.tun_rules=[r for r in rules if str(r.get("launch_mode","browser")).lower()=="tun"]
        exes=app.get("game_exes",{})
        self.exe_vars={r["id"]:tk.StringVar(value=str(exes.get(r["id"],""))) for r in self.tun_rules}

        f=ttk.Frame(self,padding=14); f.pack(fill="both",expand=True)
        box=ttk.LabelFrame(f,text="Node source",padding=10); box.pack(fill="x")
        ttk.Radiobutton(box,text="Local private\\nodes.yaml",variable=self.mode,value="file").pack(anchor="w")
        ttk.Radiobutton(box,text="Subscription URL",variable=self.mode,value="subscription").pack(anchor="w",pady=(8,0))
        ttk.Entry(box,textvariable=self.url).pack(fill="x",pady=(7,0))
        ttk.Button(box,text="Open nodes.yaml",command=lambda:os.startfile(str(PATHS.nodes_yaml))).pack(anchor="w",pady=(10,0))

        auto=ttk.LabelFrame(f,text="Japan auto-selection",padding=10); auto.pack(fill="x",pady=(12,0))
        ttk.Label(auto,text="筛选 Japan / JPN / JP / 日本 / Tokyo / Osaka / 🇯🇵，并自动测速选择最快可用日本节点。",wraplength=630).pack(anchor="w")
        row=ttk.Frame(auto); row.pack(fill="x",pady=(10,0))
        ttk.Label(row,text="Timeout (ms)").pack(side="left")
        ttk.Entry(row,textvariable=self.timeout,width=12).pack(side="left",padx=(8,0))

        if self.tun_rules:
            nk=ttk.LabelFrame(f,text="TUN 游戏可执行文件（可选，仅用于按进程名分流，不会自动启动游戏）",padding=10)
            nk.pack(fill="x",pady=(12,0))
            for r in self.tun_rules:
                row=ttk.Frame(nk); row.pack(fill="x",pady=(0,6))
                ttk.Label(row,text=r["name"],width=18).pack(side="left")
                ttk.Entry(row,textvariable=self.exe_vars[r["id"]]).pack(side="left",fill="x",expand=True)
                ttk.Button(row,text="Browse...",command=lambda gid=r["id"]:self.browse(gid)).pack(side="left",padx=(8,0))

        ttk.Label(f,text="订阅 URL、密码、UUID 等是私密凭据，不要上传公开 GitHub（打包发布请用 scripts\\MAKE_RELEASE_ZIP.ps1）。",
                  wraplength=650).pack(anchor="w",pady=(14,0))
        ttk.Button(f,text="Save",command=self.save).pack(anchor="e",pady=(14,0))

    def browse(self,gid):
        p=filedialog.askopenfilename(filetypes=[("Windows executable","*.exe"),("All files","*.*")])
        if p:self.exe_vars[gid].set(p)

    def save(self):
        try:
            timeout=int(self.timeout.get().strip())
            if timeout<1000: raise ValueError
        except ValueError:
            messagebox.showerror("FLY","Timeout 至少 1000 ms。"); return
        save_json(PATHS.node_source,{"mode":self.mode.get(),"subscription_url":self.url.get().strip()})
        app=load_app_settings(PATHS)
        exes=app.get("game_exes",{})
        for gid,var in self.exe_vars.items():
            exes[gid]=var.get().strip()
        app["game_exes"]=exes; app["latency_timeout_ms"]=timeout
        save_json(PATHS.app_settings,app)
        self.on_saved(); messagebox.showinfo("FLY","Settings saved."); self.destroy()

def main():
    ensure_private_files(PATHS)
    p=argparse.ArgumentParser(add_help=False)
    p.add_argument("--games",default="")
    p.add_argument("--game",default="")  # v0.3 compatibility
    p.add_argument("--autostart",action="store_true")
    args,_=p.parse_known_args()
    initial=[g.strip() for g in f"{args.games},{args.game}".split(",") if g.strip()]
    root=tk.Tk(); FlyApp(root,initial,args.autostart); root.mainloop()

if __name__=="__main__":
    try:
        main()
    except Exception:
        # Running windowless under pythonw: no console to die into, so keep
        # the traceback somewhere visible.
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
