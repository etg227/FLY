from __future__ import annotations
import argparse, json, os, queue, threading, time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from backend.config import (
    Paths, ensure_private_files, list_routing_profiles, load_app_settings,
    load_node_source, load_custom_profiles, save_custom_profiles,
    node_source_is_configured, save_json, app_version
)
from backend.core_installer import install_core as download_core
from backend.core_manager import CoreManager
from backend.launcher import log_hints
from backend.mihomo_api import MihomoApi, JapanNodeSelector
from backend.system_proxy import SystemProxy
from backend.windows_admin import is_admin, relaunch_as_admin

APP_DIR = Path(__file__).resolve().parent
PATHS = Paths(APP_DIR)

def _needs_tun(profile):
    return str(profile.get("launch_mode","browser")).lower() == "tun" or bool(profile.get("processes"))

class FlyApp:
    def __init__(self, root, initial_games=None, autostart=False):
        ensure_private_files(PATHS)
        self.root = root
        self.version = app_version(PATHS)
        self.root.title(f"FLY v{self.version} - Selective Routing")
        self.root.geometry("900x760")
        self.root.minsize(820, 680)

        self.logs = queue.Queue()
        self.core = CoreManager(PATHS, self.log)
        self.sysproxy = SystemProxy(PATHS.runtime / "sysproxy_backup.json", self.log)
        self.selector = None
        self.sysproxy_active = False
        self._watch_stop = None
        self._core_installing = False

        self.profiles = []
        self.profile_by_id = {}
        self.profile_vars = {}
        self.initial_ids = set(initial_games or [])

        app = load_app_settings(PATHS)
        self.routing_mode = tk.StringVar(value=app.get("routing_mode","automatic"))
        self.status_var = tk.StringVar(value="Stopped")
        self.core_var = tk.StringVar()
        self.source_var = tk.StringVar()
        self.node_var = tk.StringVar(value="-")
        self.delay_var = tk.StringVar(value="-")

        self.build()
        self.reload_profiles(first=True)
        self.refresh_status()
        self.root.after(100, self.flush_logs)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.log(f"FLY v{self.version} ready.")
        self.log("Safety rule: only explicit profile matches use FLY-JP; everything else is DIRECT.")
        self.sysproxy.restore_orphan()
        if autostart:
            self.log("[FLY] Autostart requested (admin relaunch).")
            self.root.after(600, self.start_accel)

    def build(self):
        outer = ttk.Frame(self.root, padding=16); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=f"FLY v{self.version}", font=("Segoe UI",21,"bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Use your own Clash/Mihomo nodes. Route only selected region-restricted apps/services; keep everything else local.",
            wraplength=840
        ).pack(anchor="w", pady=(0,12))

        modebox = ttk.LabelFrame(outer, text="Mode", padding=10); modebox.pack(fill="x", pady=(0,10))
        row = ttk.Frame(modebox); row.pack(fill="x")
        ttk.Radiobutton(row, text="Automatic", variable=self.routing_mode, value="automatic",
                        command=self._mode_changed).pack(side="left")
        ttk.Radiobutton(row, text="Advanced", variable=self.routing_mode, value="advanced",
                        command=self._mode_changed).pack(side="left", padx=(18,0))
        ttk.Radiobutton(row, text="Custom", variable=self.routing_mode, value="custom",
                        command=self._mode_changed).pack(side="left", padx=(18,0))
        ttk.Label(
            modebox,
            text="Automatic picks a healthy JP node. Advanced keeps manual switch/settings visible. Custom stores your own profiles locally under private/.",
            wraplength=820
        ).pack(anchor="w", pady=(6,0))

        back = ttk.LabelFrame(outer, text="Backend", padding=10); back.pack(fill="x", pady=(0,10))
        for i, (label, var) in enumerate([("Core:",self.core_var),("Node source:",self.source_var),("Japan node:",self.node_var)]):
            r = ttk.Frame(back); r.pack(fill="x", pady=(0 if i==0 else 8,0))
            ttk.Label(r,text=label,width=14).pack(side="left")
            ttk.Label(r,textvariable=var,font=("Segoe UI",10,"bold") if i==2 else None).pack(side="left")
            if i==1:
                ttk.Button(r,text="Node / App Settings",command=self.open_settings).pack(side="right")
            elif i==2:
                ttk.Label(r,text="Latency:").pack(side="left", padx=(18,4))
                ttk.Label(r,textvariable=self.delay_var).pack(side="left")

        prof = ttk.LabelFrame(outer,text="Selective routing profiles（可多选）",padding=12)
        prof.pack(fill="x", pady=(0,10))
        self.profiles_frame = ttk.Frame(prof); self.profiles_frame.pack(fill="x")
        actions = ttk.Frame(prof); actions.pack(fill="x", pady=(8,0))
        ttk.Button(actions,text="Edit local custom profiles",command=self.open_custom_editor).pack(side="left")
        ttk.Label(actions,text="Community/built-in profiles live in rules/; your custom profiles never leave private/.").pack(side="left", padx=(10,0))

        buttons = ttk.Frame(outer); buttons.pack(fill="x",pady=(2,12))
        self.start_btn = ttk.Button(buttons,text="Start routing",command=self.start_accel); self.start_btn.pack(side="left")
        ttk.Button(buttons,text="Stop",command=self.stop_accel).pack(side="left",padx=8)
        self.switch_btn = ttk.Button(buttons,text="Switch JP node",command=self.switch_node)
        self.switch_btn.pack(side="left",padx=8)
        ttk.Button(buttons,text="Refresh",command=self.refresh_status).pack(side="left",padx=8)
        ttk.Label(buttons,text="Status:").pack(side="right")
        ttk.Label(buttons,textvariable=self.status_var,font=("Segoe UI",10,"bold")).pack(side="right",padx=(0,5))

        route = ttk.LabelFrame(outer,text="Routing guarantee",padding=10); route.pack(fill="x",pady=(0,10))
        ttk.Label(route,justify="left",wraplength=840,text=(
            "Domain/IP/port/process rules are explicit. There is no full-browser catch-all. "
            "Anything that does not match a selected profile ends at MATCH,DIRECT and uses your normal network.\n"
            "Profiles with process matching use TUN and require administrator rights. Domain-only browser profiles can use the local system proxy."
        )).pack(anchor="w")

        logf = ttk.LabelFrame(outer,text="Live log",padding=8); logf.pack(fill="both",expand=True)
        self.logbox = tk.Text(logf,wrap="word",font=("Consolas",9),height=14); self.logbox.pack(side="left",fill="both",expand=True)
        sb=ttk.Scrollbar(logf,orient="vertical",command=self.logbox.yview); sb.pack(side="right",fill="y"); self.logbox.configure(yscrollcommand=sb.set)

    def _mode_changed(self):
        app = load_app_settings(PATHS)
        app["routing_mode"] = self.routing_mode.get()
        save_json(PATHS.app_settings, app)
        self.log(f"[MODE] {self.routing_mode.get()}")

    def reload_profiles(self, first=False):
        previous = {pid for pid,var in self.profile_vars.items() if var.get()}
        self.profiles = list_routing_profiles(PATHS)
        self.profile_by_id = {r["id"]:r for r in self.profiles}
        for child in self.profiles_frame.winfo_children():
            child.destroy()
        self.profile_vars = {}
        initial = self.initial_ids if first else previous
        if first and not initial and self.profiles:
            initial = {self.profiles[0]["id"]}
        for i, profile in enumerate(self.profiles):
            pid = profile["id"]
            var = tk.BooleanVar(value=pid in initial)
            self.profile_vars[pid] = var
            source = "custom" if profile.get("source")=="custom" else profile.get("category","")
            mode = "TUN" if _needs_tun(profile) else "domain"
            text = f"{profile['name']}  [{mode}{' / '+source if source else ''}]"
            ttk.Checkbutton(self.profiles_frame,text=text,variable=var).grid(
                row=i//2,column=i%2,sticky="w",padx=(0,24),pady=2
            )

    def selected_profiles(self):
        return [r["id"] for r in self.profiles if self.profile_vars.get(r["id"]) and self.profile_vars[r["id"]].get()]

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

    def latency_targets(self, selected):
        urls=[]
        for pid in selected:
            p=self.profile_by_id[pid]
            urls.extend(p.get("latency_test_urls",[]))
        fallback=str(load_app_settings(PATHS).get("latency_test_url","https://www.gstatic.com/generate_204")).strip()
        if fallback: urls.append(fallback)
        return list(dict.fromkeys(u for u in urls if u))

    def make_selector(self, selected=None):
        s=load_app_settings(PATHS)
        return JapanNodeSelector(
            MihomoApi(int(s.get("controller_port",19090)), s.get("api_secret","")),
            self.log,
            keywords=s.get("jp_keywords"),
            test_urls=self.latency_targets(selected or self.selected_profiles()),
            timeout_ms=int(s.get("latency_timeout_ms",5000))
        )

    def _ensure_core(self):
        if PATHS.core_exe.exists() or self._core_installing: return
        self._core_installing=True
        self.refresh_status()
        self.log("[CORE] Mihomo core missing; downloading from official release...")
        def work():
            try:
                download_core(PATHS,self.log)
            except Exception as e:
                self.log(f"[CORE] Download failed: {e}")
            finally:
                self._core_installing=False
                self.root.after(0,self.refresh_status)
        threading.Thread(target=work,daemon=True).start()

    def open_settings(self):
        SettingsWindow(self.root,self.profiles,self.refresh_status)

    def open_custom_editor(self):
        CustomProfilesWindow(self.root, self._custom_saved)

    def _custom_saved(self):
        self.reload_profiles(first=False)
        self.log("[CUSTOM] Local profiles reloaded.")

    def start_accel(self):
        selected=self.selected_profiles()
        if not selected:
            messagebox.showwarning("FLY","Select at least one routing profile."); return
        if not PATHS.core_exe.exists():
            self._ensure_core()
            messagebox.showinfo("FLY","Mihomo is downloading. Try Start again after it finishes."); return
        ok,_=node_source_is_configured(PATHS)
        if not ok:
            messagebox.showwarning("FLY","Configure your own node/subscription first."); return
        tun_names=[self.profile_by_id[p]["name"] for p in selected if _needs_tun(self.profile_by_id[p])]
        if tun_names and not is_admin():
            if messagebox.askyesno("FLY",f"TUN is required by {'、'.join(tun_names)}. Restart as administrator?") \
               and relaunch_as_admin(",".join(selected), autostart=True):
                self.root.after(300,self.root.destroy)
            return
        self.status_var.set("Starting..."); self.start_btn.configure(state="disabled")
        threading.Thread(target=self._start_worker,args=(selected,),daemon=True).start()

    def _start_worker(self,selected):
        try:
            names=", ".join(self.profile_by_id[p]["name"] for p in selected)
            self.log(f"[FLY] Selected profile(s): {names}")
            self.core.start(selected)
            self.selector=self.make_selector(selected)
            s=load_app_settings(PATHS)
            chosen,delay=self.selector.auto_select(
                "FLY-JP",wait_s=45,
                preferred=str(s.get("last_node","")).strip() or None,
                sticky_max_delay_ms=int(s.get("sticky_max_delay_ms",1000))
            )
            self._remember_node(chosen)
            self.root.after(0,lambda n=chosen:self.node_var.set(n))
            self.root.after(0,lambda d=delay:self.delay_var.set(f"{d} ms" if d is not None else "unknown"))

            uses_tun=any(_needs_tun(self.profile_by_id[p]) for p in selected)
            has_domain_only=any(not _needs_tun(self.profile_by_id[p]) for p in selected)
            if has_domain_only and not uses_tun:
                port=int(s.get("mixed_port",17890))
                self.sysproxy.enable(port); self.sysproxy_active=True
            log_hints(PATHS,selected,self.log)
            self._start_watchdog()
            self.root.after(0,lambda:self.status_var.set(f"Routing ({len(selected)})"))
        except Exception as e:
            self.log(f"[ERROR] {e}")
            self._teardown()
            self.root.after(0,lambda:self.status_var.set("Stopped"))
            self.root.after(0,lambda:messagebox.showerror("FLY",str(e)))
        finally:
            self.root.after(0,lambda:self.start_btn.configure(state="normal"))

    def switch_node(self):
        if not self.core.is_running():
            messagebox.showinfo("FLY","Start routing first."); return
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
                sel.measure(current); fails=0
            except Exception:
                fails+=1
                self.log(f"[WATCH] Node check failed ({fails}/3).")
                if fails>=3:
                    fails=0
                    self.log("[WATCH] Reselecting a healthy Japan node...")
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
        if self.sysproxy_active:
            self.sysproxy.restore(); self.sysproxy_active=False
        self.core.stop(); self.selector=None

    def stop_accel(self):
        self._teardown()
        self.node_var.set("-"); self.delay_var.set("-"); self.status_var.set("Stopped")
        self.log("[FLY] Routing stopped.")

    def on_close(self):
        self._teardown(); self.root.destroy()

class SettingsWindow(tk.Toplevel):
    def __init__(self,master,profiles,on_saved):
        super().__init__(master)
        self.title("FLY - Node / App Settings"); self.geometry("720x620")
        ensure_private_files(PATHS)
        src=load_node_source(PATHS); app=load_app_settings(PATHS)
        self.on_saved=on_saved
        self.mode=tk.StringVar(value=src.get("mode","file"))
        self.url=tk.StringVar(value=src.get("subscription_url",""))
        self.timeout=tk.StringVar(value=str(app.get("latency_timeout_ms",5000)))
        self.sticky=tk.StringVar(value=str(app.get("sticky_max_delay_ms",1000)))
        self.tun_profiles=[r for r in profiles if _needs_tun(r)]
        exes=app.get("game_exes",{})
        self.exe_vars={r["id"]:tk.StringVar(value=str(exes.get(r["id"],""))) for r in self.tun_profiles}

        f=ttk.Frame(self,padding=14); f.pack(fill="both",expand=True)
        box=ttk.LabelFrame(f,text="Your node source",padding=10); box.pack(fill="x")
        ttk.Radiobutton(box,text="Local private\\nodes.yaml",variable=self.mode,value="file").pack(anchor="w")
        ttk.Radiobutton(box,text="Clash/Mihomo subscription URL",variable=self.mode,value="subscription").pack(anchor="w",pady=(8,0))
        ttk.Entry(box,textvariable=self.url).pack(fill="x",pady=(7,0))
        ttk.Button(box,text="Open nodes.yaml",command=lambda:os.startfile(str(PATHS.nodes_yaml))).pack(anchor="w",pady=(10,0))

        auto=ttk.LabelFrame(f,text="Automatic / Advanced selection",padding=10); auto.pack(fill="x",pady=(12,0))
        ttk.Label(auto,text="FLY only filters nodes labelled Japan/JPN/JP/日本/Tokyo/Osaka. Service-specific latency targets come from the selected profiles.",wraplength=650).pack(anchor="w")
        rr=ttk.Frame(auto); rr.pack(fill="x",pady=(10,0))
        ttk.Label(rr,text="Timeout (ms)").pack(side="left"); ttk.Entry(rr,textvariable=self.timeout,width=10).pack(side="left",padx=(8,18))
        ttk.Label(rr,text="Keep last node if ≤").pack(side="left"); ttk.Entry(rr,textvariable=self.sticky,width=10).pack(side="left",padx=(8,4)); ttk.Label(rr,text="ms").pack(side="left")

        if self.tun_profiles:
            nk=ttk.LabelFrame(f,text="Optional executable override for TUN profiles",padding=10)
            nk.pack(fill="x",pady=(12,0))
            for r in self.tun_profiles:
                rr=ttk.Frame(nk); rr.pack(fill="x",pady=(0,6))
                ttk.Label(rr,text=r["name"],width=22).pack(side="left")
                ttk.Entry(rr,textvariable=self.exe_vars[r["id"]]).pack(side="left",fill="x",expand=True)
                ttk.Button(rr,text="Browse...",command=lambda pid=r["id"]:self.browse(pid)).pack(side="left",padx=(8,0))

        ttk.Label(f,text="Credentials stay in private/ and are excluded from Git. FLY does not provide nodes, accounts, membership or VPS service.",
                  wraplength=670).pack(anchor="w",pady=(14,0))
        ttk.Button(f,text="Save",command=self.save).pack(anchor="e",pady=(14,0))

    def browse(self,pid):
        p=filedialog.askopenfilename(filetypes=[("Windows executable","*.exe"),("All files","*.*")])
        if p:self.exe_vars[pid].set(p)

    def save(self):
        try:
            timeout=int(self.timeout.get().strip()); sticky=int(self.sticky.get().strip())
            if timeout<1000 or sticky<100: raise ValueError
        except ValueError:
            messagebox.showerror("FLY","Use valid timeout/sticky values."); return
        save_json(PATHS.node_source,{"mode":self.mode.get(),"subscription_url":self.url.get().strip()})
        app=load_app_settings(PATHS)
        exes=app.get("game_exes",{})
        for pid,var in self.exe_vars.items():
            exes[pid]=var.get().strip()
        app["game_exes"]=exes
        app["latency_timeout_ms"]=timeout
        app["sticky_max_delay_ms"]=sticky
        save_json(PATHS.app_settings,app)
        self.on_saved(); messagebox.showinfo("FLY","Settings saved."); self.destroy()

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
        self.title("FLY - Local custom profiles"); self.geometry("780x650")
        self.on_saved=on_saved
        f=ttk.Frame(self,padding=12); f.pack(fill="both",expand=True)
        ttk.Label(f,text="Local JSON profile editor",font=("Segoe UI",13,"bold")).pack(anchor="w")
        ttk.Label(f,text="This file is private/custom_profiles.json and is never committed. Use domains/processes/IP/ports only for traffic you explicitly want to proxy.",wraplength=740).pack(anchor="w",pady=(4,8))
        self.text=tk.Text(f,wrap="none",font=("Consolas",10))
        self.text.pack(fill="both",expand=True)
        current={"profiles":[{k:v for k,v in p.items() if k!="source"} for p in load_custom_profiles(PATHS)]}
        if not current["profiles"]: current=self.TEMPLATE
        self.text.insert("1.0",json.dumps(current,ensure_ascii=False,indent=2))
        row=ttk.Frame(f); row.pack(fill="x",pady=(8,0))
        ttk.Button(row,text="Save local profiles",command=self.save).pack(side="right")

    def save(self):
        try:
            data=json.loads(self.text.get("1.0","end"))
            if not isinstance(data,dict) or not isinstance(data.get("profiles"),list):
                raise ValueError("Top level must contain a profiles array.")
            ids=set()
            for p in data["profiles"]:
                if not isinstance(p,dict): raise ValueError("Every profile must be an object.")
                pid=str(p.get("id","")).strip(); name=str(p.get("name","")).strip()
                if not pid or not name: raise ValueError("Every profile needs id and name.")
                if pid in ids: raise ValueError(f"Duplicate profile id: {pid}")
                ids.add(pid)
            save_custom_profiles(PATHS,data["profiles"])
        except Exception as e:
            messagebox.showerror("FLY",f"Invalid profile JSON:\n{e}"); return
        self.on_saved(); messagebox.showinfo("FLY","Custom profiles saved locally."); self.destroy()

def main():
    ensure_private_files(PATHS)
    p=argparse.ArgumentParser(add_help=False)
    p.add_argument("--games",default="")
    p.add_argument("--game",default="")
    p.add_argument("--autostart",action="store_true")
    args,_=p.parse_known_args()
    initial=[g.strip() for g in f"{args.games},{args.game}".split(",") if g.strip()]
    root=tk.Tk(); FlyApp(root,initial,args.autostart); root.mainloop()

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
            messagebox.showerror("FLY",f"Startup failed (see runtime\\error.log):\n\n{err[-1500:]}")
        except Exception: pass
