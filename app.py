from tkinter import ttk, filedialog, messagebox
import os
import tkinter as tk
import queue
import threading

from crawler import ThreadsCrawler

# ==============================================
# DESKTOP APPLICATION
# ==============================================

class App:

    def __init__(self, root):

        self.root = root

        self.root.title("ThreadImageCrawler")

        self.root.geometry("720x570")

        self.root.minsize(650, 500)

        self.events = queue.Queue()

        self.worker = None

        self.running = False

        self.output_dir = os.path.join(
            os.path.expanduser("~"),
            "Pictures",
            "Threads"
        )

        self.build_ui()

        self.root.after(
            100,
            self.process_events
        )

    # ==========================================
    # GUI
    # ==========================================

    def build_ui(self):

        main = ttk.Frame(
            self.root,
            padding=20
        )

        main.pack(
            fill="both",
            expand=True
        )

        ttk.Label(
            main,
            text="THREAD IMAGE CRAWLER",
            font=("Segoe UI", 19, "bold")
        ).pack(anchor="w")

        ttk.Label(
            main,
            text="Download publicly accessible images from Threads",
            foreground="gray"
        ).pack(
            anchor="w",
            pady=(0, 20)
        )

        # Profile URL

        ttk.Label(
            main,
            text="Threads Profile URL / Username"
        ).pack(anchor="w")

        self.url_entry = ttk.Entry(
            main,
            font=("Segoe UI", 11)
        )

        self.url_entry.pack(
            fill="x",
            pady=8
        )

        self.url_entry.insert(
            0,
            "https://www.threads.com/@username"
        )

        # Output folder

        ttk.Label(
            main,
            text="Output folder"
        ).pack(anchor="w")

        folder_frame = ttk.Frame(main)

        folder_frame.pack(
            fill="x",
            pady=8
        )

        self.folder_entry = ttk.Entry(
            folder_frame
        )

        self.folder_entry.pack(
            side="left",
            fill="x",
            expand=True
        )

        self.folder_entry.insert(
            0,
            self.output_dir
        )

        ttk.Button(
            folder_frame,
            text="Browse",
            command=self.choose_folder
        ).pack(
            side="right",
            padx=(8, 0)
        )

        # Maximum scrolls

        ttk.Label(
            main,
            text="Maximum scroll rounds"
        ).pack(
            anchor="w",
            pady=(10, 0)
        )

        self.scroll_var = tk.IntVar(
            value=100
        )

        ttk.Spinbox(
            main,
            from_=5,
            to=500,
            textvariable=self.scroll_var,
            width=10
        ).pack(
            anchor="w",
            pady=8
        )

        # Buttons

        button_frame = ttk.Frame(main)

        button_frame.pack(
            fill="x",
            pady=12
        )

        self.start_btn = ttk.Button(
            button_frame,
            text="Start Crawling",
            command=self.start
        )

        self.start_btn.pack(side="left")

        self.stop_btn = ttk.Button(
            button_frame,
            text="Stop",
            command=self.stop,
            state="disabled"
        )

        self.stop_btn.pack(
            side="left",
            padx=10
        )

        # Progress

        self.progress = ttk.Progressbar(
            main,
            mode="determinate"
        )

        self.progress.pack(
            fill="x",
            pady=10
        )

        self.status = ttk.Label(
            main,
            text="Ready"
        )

        self.status.pack(anchor="w")

        # Activity log

        ttk.Label(
            main,
            text="Activity Log"
        ).pack(
            anchor="w",
            pady=(15, 5)
        )

        self.log_box = tk.Text(
            main,
            height=12,
            font=("Consolas", 9),
            state="disabled"
        )

        self.log_box.pack(
            fill="both",
            expand=True
        )

    # ==========================================
    # SELECT OUTPUT FOLDER
    # ==========================================

    def choose_folder(self):

        folder = filedialog.askdirectory()

        if folder:

            self.folder_entry.delete(
                0,
                "end"
            )

            self.folder_entry.insert(
                0,
                folder
            )

    # ==========================================
    # EVENT CALLBACKS
    # ==========================================

    def log(self, message):

        self.events.put(
            ("log", message)
        )

    def update_progress(self, current, total):

        self.events.put(
            ("progress", current, total)
        )

    def finished(
        self,
        downloaded,
        failed,
        folder,
        cancelled
    ):

        self.events.put((
            "finished",
            downloaded,
            failed,
            folder,
            cancelled
        ))

    # ==========================================
    # START CRAWLER
    # ==========================================

    def start(self):

        if self.running:
            return

        url = self.url_entry.get().strip()

        folder = self.folder_entry.get().strip()

        if not url or not folder:

            messagebox.showwarning(
                "Warning",
                "Vui lòng nhập URL và thư mục lưu."
            )

            return

        if not os.path.isdir(folder):

            messagebox.showwarning(
                "Warning",
                "Thư mục lưu không tồn tại."
            )

            return

        try:

            rounds = int(
                self.scroll_var.get()
            )

            if not 5 <= rounds <= 500:
                raise ValueError()

        except ValueError:

            messagebox.showwarning(
                "Warning",
                "Số lần cuộn phải từ 5 đến 500."
            )

            return

        self.running = True

        self.progress["value"] = 0

        self.status.config(
            text="Connecting..."
        )

        self.start_btn.config(
            state="disabled"
        )

        self.stop_btn.config(
            state="normal"
        )

        self.worker = ThreadsCrawler(
            self.log,
            self.update_progress,
            self.finished
        )

        threading.Thread(
            target=self.worker.crawl,
            args=(url, folder, rounds),
            daemon=True
        ).start()

    # ==========================================
    # STOP CRAWLER
    # ==========================================

    def stop(self):

        if self.worker:

            self.worker.cancel_event.set()

            self.log(
                "Đang yêu cầu dừng tác vụ..."
            )

    # ==========================================
    # PROCESS EVENTS
    # ==========================================

    def process_events(self):

        try:

            while True:

                event = self.events.get_nowait()

                # Log

                if event[0] == "log":

                    self.log_box.config(
                        state="normal"
                    )

                    self.log_box.insert(
                        "end",
                        event[1] + "\n"
                    )

                    self.log_box.see("end")

                    self.log_box.config(
                        state="disabled"
                    )

                # Progress

                elif event[0] == "progress":

                    current = event[1]

                    total = event[2]

                    self.progress["maximum"] = max(
                        total,
                        1
                    )

                    self.progress["value"] = current

                    self.status.config(
                        text=f"Downloading: {current}/{total}"
                    )

                # Finished

                elif event[0] == "finished":

                    (
                        _,
                        downloaded,
                        failed,
                        folder,
                        cancelled
                    ) = event

                    self.running = False

                    self.start_btn.config(
                        state="normal"
                    )

                    self.stop_btn.config(
                        state="disabled"
                    )

                    if cancelled:

                        self.status.config(
                            text="Stopped"
                        )

                    else:

                        self.status.config(
                            text="Completed"
                        )

                    self.log(
                        f"Hoàn tất: {downloaded} thành công, "
                        f"{failed} thất bại."
                    )

                    if folder:

                        self.log(
                            f"Thư mục lưu: {folder}"
                        )

        except queue.Empty:
            pass

        self.root.after(
            100,
            self.process_events
        )

