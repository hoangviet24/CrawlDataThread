from urllib.parse import urlparse
import tkinter as tk
from playwright.sync_api import sync_playwright

from app import App
    

# ==============================================
# APPLICATION ENTRY POINT
# ==============================================

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
