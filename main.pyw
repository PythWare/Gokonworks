import os, threading, tkinter as tk
from tkinter import messagebox
from Gokonworks import CoreTools
from Gokonworks.ledger import ensure_backups, log, LOG_PATH

"""Main script the user will use to call other scripts"""

def install_tk_exception_hook(log, show_messagebox=True):
    def handler(self, exc, val, tb):
        log.error("Tk callback exception (%s)", type(self).__name__, exc_info=(exc, val, tb))
        if show_messagebox:
            try:
                messagebox.showerror("Mixer Error", "Something went wrong. Details were written to the log file.")
            except Exception:
                pass

    tk.Misc.report_callback_exception = handler

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    root = tk.Tk()

    install_tk_exception_hook(log)
    backups_ok, backup_level, backup_msg = ensure_backups()
    if backup_msg:
        dialog_map = {
            "info": messagebox.showinfo,
            "warning": messagebox.showwarning,
            "error": messagebox.showerror,
        }
        dialog_map.get(backup_level, messagebox.showwarning)("Backup Check", backup_msg)

    popup_lock = threading.Lock()
    popup_active = False

    def thread_excepthook(args):
        nonlocal popup_active
        log.error(
            "Unhandled exception in thread %s",
            getattr(args.thread, "name", "<unknown>"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )

        def show():
            nonlocal popup_active
            with popup_lock:
                if popup_active:
                    return
                popup_active = True
            try:
                root.after(0, lambda: messagebox.showerror("Mixer Error", f"Something went wrong.\nLog:\n{LOG_PATH}"))
            finally:
                with popup_lock:
                    popup_active = False

        try:
            if root.winfo_exists():
                root.after(0, show)
        except Exception:
            pass

    threading.excepthook = thread_excepthook

    app = CoreTools(root)
    root.mainloop()

if __name__ == "__main__":
    main()

