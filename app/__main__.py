from __future__ import annotations

import ctypes
import logging
import os
import socket
import subprocess
import sys
import threading
import time

import uvicorn

from app.desktop import DesktopBridge
from app.home import get_home, sweep_cache
from app.main import app, resource_root


class AppServer(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        return


def choose_port() -> int:
    preferred = int(os.environ.get("RF_PORT", "8765"))
    for port in range(preferred, preferred + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit("端口 8765–8794 都被占用了")


def start_server(port: int) -> AppServer:
    log_path = get_home() / "app.log"
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(message)s"}},
            "handlers": {
                "file": {
                    "class": "logging.FileHandler",
                    "filename": str(log_path),
                    "encoding": "utf-8",
                    "formatter": "plain",
                }
            },
            "loggers": {
                "uvicorn": {"handlers": ["file"], "level": "INFO"},
                "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
            },
        },
    )
    server = AppServer(config)
    threading.Thread(target=server.run, name="remote-folder-http", daemon=True).start()
    return server


def wait_until_ready(port: int) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError("本地服务没有在 15 秒内启动")


def show_error(message: str) -> None:
    logging.exception(message) if sys.exc_info()[0] else logging.error(message)
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "remote-folder", 0x10)


def configure_webview_runtime(webview) -> None:
    """Use the runtime shipped inside the EXE, even when Windows has none installed."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    runtime = resource_root() / "webview2"
    if not (runtime / "msedgewebview2.exe").is_file():
        raise RuntimeError("程序内置的窗口运行时不完整，请重新下载完整的 EXE。")
    # Fixed WebView2 v120+ uses AppContainer on Windows 10. Microsoft requires
    # these read/execute permissions on the extracted runtime directory.
    # https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution
    if sys.getwindowsversion().build < 22000:
        subprocess.run(
            [
                "icacls.exe",
                str(runtime),
                "/grant",
                "*S-1-15-2-2:(OI)(CI)(RX)",
                "*S-1-15-2-1:(OI)(CI)(RX)",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=60,
        )
    webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(runtime)
    logging.info("Using bundled WebView2 runtime: %s", runtime)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("remote-folder")
    log_path = get_home() / "app.log"
    logging.basicConfig(
        filename=str(log_path),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )
    try:
        import webview

        webview.settings["ALLOW_DOWNLOADS"] = True
    except ImportError:
        if getattr(sys, "frozen", False):
            show_error("窗口组件没有随程序一起打包。")
        else:
            show_error("缺少窗口组件。请在项目目录重新运行 run.bat。")
        raise SystemExit(1)

    try:
        configure_webview_runtime(webview)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        show_error(f"内置窗口运行时初始化失败：{exc}")
        raise SystemExit(1)

    sweep_cache()
    port = choose_port()
    server = start_server(port)
    try:
        wait_until_ready(port)
        bridge = DesktopBridge()
        window = webview.create_window(
            "remote-folder",
            f"http://127.0.0.1:{port}",
            width=1180,
            height=760,
            min_size=(960, 640),
            background_color="#eef1f4",
            text_select=True,
            js_api=bridge,
        )
        bridge._window = window
        if window is not None:
            window.events.closed += lambda: setattr(server, "should_exit", True)
        webview.start(gui="edgechromium")
    except Exception as exc:
        show_error(f"窗口没有打开：{exc}")
        raise SystemExit(1)
    finally:
        server.should_exit = True


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
