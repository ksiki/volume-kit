import os
import sys
import traceback


def _set_windows_app_id() -> None:
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "TradeHelper.VolumeKit.1"
            )
        except Exception:
            pass


def _show_fatal_error(msg: str) -> None:
    if sys.platform.startswith("win") and getattr(sys, "frozen", False):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "Volume Kit — Ошибка", 0x10)
            return
        except Exception:
            pass
    print(msg, file=sys.stderr)


if __name__ == "__main__":
    cli_mode = "--cli" in sys.argv

    if not cli_mode:
        _set_windows_app_id()
        if getattr(sys, "frozen", False):
            if sys.stdout is None:
                sys.stdout = open(os.devnull, "w", encoding="utf-8")
            if sys.stderr is None:
                sys.stderr = open(os.devnull, "w", encoding="utf-8")

    try:
        if cli_mode:
            from app.main import main
            main()
        else:
            from app.gui import start_gui
            start_gui()
    except Exception:
        tb = traceback.format_exc()
        _show_fatal_error(tb)
    finally:
        if cli_mode and getattr(sys, "frozen", False):
            input("\nНажмите Enter для выхода...")