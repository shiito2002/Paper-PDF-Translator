#! python3
"""論文PDF翻訳ソフトの画面版。ダブルクリックで起動する。

Product by shiito
"""

import json
import os
import queue
import threading
import tkinter as tk
import traceback
import webbrowser
from tkinter import filedialog, messagebox, ttk

import translate_pdf

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # ドラッグ＆ドロップなしでも動くようにする
    TkinterDnD = None

ENGINES = {
    "Claude（サブスク枠・高品質）": "claude",
    "DeepL（無料API）": "deepl",
}
DEEPL_SIGNUP = "https://www.deepl.com/ja/pro-api"
MODELS = {
    "Sonnet（標準）": "sonnet",
    "Opus（高品質・遅い）": "opus",
    "Haiku（高速）": "haiku",
}
UI_FONT = ("Yu Gothic UI", 10)
CREDIT = "Product by shiito"
SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "論文翻訳_設定.json")


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.worker = None
        self.outputs = {}  # 元PDFのパス -> 出力PDFのパス

        root.title("論文PDF翻訳")
        root.geometry("720x620")
        root.minsize(560, 480)
        style = ttk.Style()
        style.configure(".", font=UI_FONT)
        style.configure("Treeview", rowheight=24)
        style.configure("Accent.TButton", font=(UI_FONT[0], 11, "bold"))

        main = ttk.Frame(root, padding=12)
        main.pack(fill="both", expand=True)

        # --- クレジット（最初に下端へ置き、ウィンドウを縮めても隠れないようにする）
        tk.Label(main, text=CREDIT, font=(UI_FONT[0], 8), fg="#999").pack(side="bottom", anchor="e")

        # --- ドロップ領域
        self.drop = tk.Label(
            main, text="ここに論文PDFをドラッグ＆ドロップ（複数可）",
            font=(UI_FONT[0], 12), fg="#555", bg="#f3f6fa",
            relief="groove", bd=2, height=3, cursor="hand2")
        self.drop.pack(fill="x")
        self.drop.bind("<Button-1>", lambda e: self.choose_files())
        if TkinterDnD:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self.on_drop)
            self.drop.dnd_bind("<<DropEnter>>", lambda e: self.drop.config(bg="#dde8f7"))
            self.drop.dnd_bind("<<DropLeave>>", lambda e: self.drop.config(bg="#f3f6fa"))
        else:
            self.drop.config(text="クリックして論文PDFを選択（複数可）")

        # --- ファイル一覧
        cols = ("status",)
        self.tree = ttk.Treeview(main, columns=cols, height=6, selectmode="browse")
        self.tree.heading("#0", text="ファイル")
        self.tree.heading("status", text="状態")
        self.tree.column("#0", width=480)
        self.tree.column("status", width=150, anchor="center")
        self.tree.pack(fill="x", pady=(10, 0))
        self.tree.bind("<Double-1>", lambda e: self.open_output())

        row = ttk.Frame(main)
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="ファイルを追加…", command=self.choose_files).pack(side="left")
        ttk.Button(row, text="一覧から外す", command=self.remove_selected).pack(side="left", padx=4)
        self.open_btn = ttk.Button(row, text="訳したPDFを開く", command=self.open_output)
        self.open_btn.pack(side="right")
        ttk.Button(row, text="フォルダを開く", command=self.open_folder).pack(side="right", padx=4)

        # --- 設定
        opts = ttk.LabelFrame(main, text="設定", padding=8)
        opts.pack(fill="x", pady=(10, 0))
        row1 = ttk.Frame(opts)
        row1.pack(fill="x")
        ttk.Label(row1, text="翻訳エンジン:").pack(side="left")
        self.engine = ttk.Combobox(row1, values=list(ENGINES), state="readonly", width=24)
        self.engine.pack(side="left", padx=(4, 16))
        self.engine.bind("<<ComboboxSelected>>", lambda e: self.on_engine())

        # Claude 用の設定
        self.claude_opts = ttk.Frame(row1)
        ttk.Label(self.claude_opts, text="モデル:").pack(side="left")
        self.model = ttk.Combobox(self.claude_opts, values=list(MODELS), state="readonly", width=20)
        self.model.pack(side="left", padx=4)

        # DeepL 用の設定
        self.deepl_opts = ttk.Frame(row1)
        ttk.Label(self.deepl_opts, text="APIキー:").pack(side="left")
        self.deepl_key = tk.StringVar()
        self.key_entry = ttk.Entry(self.deepl_opts, textvariable=self.deepl_key, show="•", width=26)
        self.key_entry.pack(side="left", padx=4)
        self.show_key = tk.BooleanVar()
        ttk.Checkbutton(self.deepl_opts, text="表示", variable=self.show_key,
                        command=lambda: self.key_entry.config(
                            show="" if self.show_key.get() else "•")).pack(side="left")
        ttk.Button(self.deepl_opts, text="キーを取得…", width=10,
                   command=lambda: webbrowser.open(DEEPL_SIGNUP)).pack(side="left", padx=(4, 0))

        row2 = ttk.Frame(opts)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="保存先:").pack(side="left")
        self.out_mode = tk.StringVar(value="same")
        ttk.Radiobutton(row2, text="元のPDFと同じフォルダ", value="same",
                        variable=self.out_mode, command=self.on_out_mode).pack(side="left", padx=(4, 8))
        ttk.Radiobutton(row2, text="指定フォルダ:", value="custom",
                        variable=self.out_mode, command=self.on_out_mode).pack(side="left")
        self.out_dir = tk.StringVar()
        self.out_entry = ttk.Entry(row2, textvariable=self.out_dir)
        self.out_entry.pack(side="left", fill="x", expand=True, padx=4)
        self.out_btn = ttk.Button(row2, text="参照…", command=self.choose_out_dir)
        self.out_btn.pack(side="left")
        self.load_settings()

        # --- 実行と進み具合
        self.start_btn = ttk.Button(main, text="翻訳開始", style="Accent.TButton",
                                    command=self.start)
        self.start_btn.pack(fill="x", pady=(10, 0), ipady=4)
        self.progress = ttk.Progressbar(main, maximum=1.0)
        self.progress.pack(fill="x", pady=(8, 0))
        self.status = ttk.Label(main, text="PDFを追加してください")
        self.status.pack(anchor="w", pady=(2, 0))

        # --- ログ
        logf = ttk.Frame(main)
        logf.pack(fill="both", expand=True, pady=(8, 0))
        self.log = tk.Text(logf, height=8, font=("Consolas", 9), state="disabled",
                           wrap="char", bg="#fafafa")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll)

    # ------------------------------------------------ 設定・保存先

    def load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        engine = next((k for k, v in ENGINES.items() if v == st.get("engine")), None)
        self.engine.set(engine or next(iter(ENGINES)))
        model = st.get("model")
        self.model.set(model if model in MODELS else next(iter(MODELS)))
        self.deepl_key.set(st.get("deepl_key", ""))
        self.out_mode.set(st.get("out_mode", "same"))
        self.out_dir.set(st.get("out_dir", ""))
        self.on_engine()
        self.on_out_mode()

    def save_settings(self):
        st = {"engine": ENGINES[self.engine.get()], "model": self.model.get(),
              "deepl_key": self.deepl_key.get().strip(),
              "out_mode": self.out_mode.get(), "out_dir": self.out_dir.get()}
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def on_engine(self):
        """選んだエンジンに合わせて、モデル欄と APIキー欄を切り替える。"""
        deepl = ENGINES[self.engine.get()] == "deepl"
        self.claude_opts.pack_forget()
        self.deepl_opts.pack_forget()
        (self.deepl_opts if deepl else self.claude_opts).pack(side="left")

    def on_out_mode(self):
        state = "normal" if self.out_mode.get() == "custom" else "disabled"
        self.out_entry.config(state=state)
        self.out_btn.config(state=state)
        if state == "normal" and not self.out_dir.get().strip():
            self.choose_out_dir()

    def choose_out_dir(self):
        d = filedialog.askdirectory(title="訳したPDFの保存先フォルダを選択",
                                    initialdir=self.out_dir.get() or None)
        if d:
            self.out_dir.set(os.path.normpath(d))
        elif not self.out_dir.get().strip():
            self.out_mode.set("same")
            self.on_out_mode()

    def out_path(self, src):
        """設定に従った出力PDFのパス。"""
        default = translate_pdf.output_path(src)
        d = self.out_dir.get().strip()
        if self.out_mode.get() == "custom" and d:
            return os.path.join(d, os.path.basename(default))
        return default

    # ------------------------------------------------ ファイル一覧

    def add_files(self, paths):
        for p in paths:
            p = os.path.abspath(p)
            if not p.lower().endswith(".pdf") or p.endswith("_日本語訳.pdf"):
                continue
            if not self.tree.exists(p):
                out = self.out_path(p)
                done = os.path.exists(out)
                self.tree.insert("", "end", iid=p, text=os.path.basename(p),
                                 values=("翻訳済み（再実行可）" if done else "待機中",))
                if done:
                    self.outputs[p] = out
        if self.tree.get_children() and not self.busy():
            self.status.config(text=f"{len(self.tree.get_children())}件のPDF。「翻訳開始」を押してください")

    def on_drop(self, event):
        self.drop.config(bg="#f3f6fa")
        self.add_files(self.root.tk.splitlist(event.data))

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="論文PDFを選択", filetypes=[("PDF", "*.pdf")])
        self.add_files(paths)

    def remove_selected(self):
        for iid in self.tree.selection():
            if not (self.busy() and self.tree.set(iid, "status") == "翻訳中"):
                self.tree.delete(iid)

    def selected_or_last(self):
        sel = self.tree.selection()
        if sel:
            return sel[0]
        done = [i for i in self.tree.get_children() if i in self.outputs]
        return done[-1] if done else None

    def open_output(self):
        src = self.selected_or_last()
        out = self.outputs.get(src)
        if out and os.path.exists(out):
            os.startfile(out)
        else:
            messagebox.showinfo("論文PDF翻訳", "翻訳済みのPDFがまだありません。")

    def open_folder(self):
        src = self.selected_or_last() or next(iter(self.tree.get_children()), None)
        if src:
            out = self.outputs.get(src) or self.out_path(src)
            folder = os.path.dirname(out)
            os.startfile(folder if os.path.isdir(folder) else os.path.dirname(src))

    # ------------------------------------------------ 実行

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def start(self):
        files = list(self.tree.get_children())
        if not files:
            self.choose_files()
            return
        if self.out_mode.get() == "custom":
            d = self.out_dir.get().strip()
            if not d:
                messagebox.showwarning("論文PDF翻訳", "保存先フォルダを指定してください。")
                return
            try:
                os.makedirs(d, exist_ok=True)
            except OSError as e:
                messagebox.showerror("論文PDF翻訳", f"保存先フォルダを作れません:\n{e}")
                return
        engine = ENGINES[self.engine.get()]
        if engine == "deepl" and not self.deepl_key.get().strip():
            messagebox.showwarning(
                "論文PDF翻訳",
                "DeepL の APIキーを入力してください。\n"
                "「キーを取得…」から DeepL API Free に登録すると無料で取得できます。")
            return
        jobs = [(src, self.out_path(src)) for src in files]
        options = {"engine": engine, "model": MODELS[self.model.get()],
                   "deepl_key": self.deepl_key.get().strip()}
        self.save_settings()
        self.start_btn.config(state="disabled", text="翻訳中…")
        self.worker = threading.Thread(target=self.run, args=(jobs, options), daemon=True)
        self.worker.start()

    def run(self, jobs, options):
        """別スレッドで実行。画面の更新は self.events 経由で行う。"""
        put = self.events.put
        for i, (src, out) in enumerate(jobs):
            def progress(f, i=i):
                put(("progress", (i + f * 0.9) / len(jobs)))
            put(("state", src, "翻訳中"))
            put(("status", f"翻訳中 ({i + 1}/{len(jobs)}): {os.path.basename(src)}"))
            try:
                out = translate_pdf.process(src, out, **options,
                                            log=lambda m: put(("log", m)),
                                            progress=progress)
                put(("done", src, out))
            except (SystemExit, RuntimeError) as e:  # claude が見つからない、APIキー誤り等
                put(("error", src, str(e)))
            except Exception as e:
                put(("log", traceback.format_exc()))
                put(("error", src, str(e).splitlines()[0] if str(e) else repr(e)))
        put(("finished", None))

    def poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self.write_log(ev[1])
                elif kind == "progress":
                    self.progress["value"] = ev[1]
                elif kind == "status":
                    self.status.config(text=ev[1])
                elif kind == "state" and self.tree.exists(ev[1]):
                    self.tree.set(ev[1], "status", ev[2])
                elif kind == "done":
                    self.outputs[ev[1]] = ev[2]
                    if self.tree.exists(ev[1]):
                        self.tree.set(ev[1], "status", "完了 ✔")
                        self.tree.selection_set(ev[1])
                elif kind == "error":
                    if self.tree.exists(ev[1]):
                        self.tree.set(ev[1], "status", "エラー")
                    self.write_log("エラー: " + ev[2])
                elif kind == "finished":
                    self.progress["value"] = 1.0
                    self.start_btn.config(state="normal", text="翻訳開始")
                    errors = [i for i in self.tree.get_children()
                              if self.tree.set(i, "status") == "エラー"]
                    self.status.config(text="完了しました。ダブルクリックで訳したPDFを開けます"
                                       if not errors else f"{len(errors)}件でエラーが発生しました（ログを確認）")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def write_log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def on_close(self):
        if self.busy() and not messagebox.askyesno(
                "論文PDF翻訳", "翻訳中です。終了しますか？\n（途中までの翻訳は保存され、次回続きから再開します）"):
            return
        self.save_settings()
        self.root.destroy()


def main():
    try:  # 高DPI画面で文字がぼやけないようにする（ウィンドウ作成前に設定する）
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
