import socket
import threading
import webbrowser


def is_already_running() -> bool:
    """Check if the server is already running by attempting a TCP connection to localhost:8000."""
    try:
        with socket.create_connection(("localhost", 8000), timeout=0.5):
            return True
    except OSError:
        return False


def open_browser() -> None:
    """Open http://localhost:8000 in the default browser after a short delay (non-blocking)."""
    def _open():
        import time
        time.sleep(1.5)
        webbrowser.open("http://localhost:8000")

    threading.Thread(target=_open, daemon=True).start()


def run_tray_icon(shutdown_event: threading.Event) -> None:
    """Create and run a system tray icon. Blocks until the icon is stopped."""
    import pystray

    def _make_icon_image():
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (64, 64), color=(52, 152, 219))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, 60, 60], fill=(255, 255, 255))
        return img

    def on_open(_icon, _item):
        webbrowser.open("http://localhost:8000")

    def on_quit(_icon, _item):
        shutdown_event.set()
        _icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("打开界面", on_open),
        pystray.MenuItem("退出", on_quit),
    )
    icon = pystray.Icon("SmartHome", _make_icon_image(), "SmartHome", menu)
    icon.run()
