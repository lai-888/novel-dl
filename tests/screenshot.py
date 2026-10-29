"""给网页界面截图，用于肉眼检查排版。

用 Chrome / Edge 的 headless 截图，无需额外依赖。

跑：python tests/screenshot.py [输出路径]
"""
import io
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser():
    for p in CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "ui.png")
    browser = find_browser()
    if not browser:
        print("找不到 Chrome/Edge，无法截图")
        return 1
    print("浏览器:", browser)

    import webui
    from http.server import ThreadingHTTPServer
    srv = None
    for port in range(8950, 8960):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), webui.Handler)
            break
        except OSError:
            continue
    if not srv:
        print("端口占用"); return 1
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print("页面:", url)
    time.sleep(0.5)

    tmp_profile = os.path.join(os.environ.get("TEMP", "."), "shot_profile")
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--window-size=820,1000",
        f"--screenshot={out}",
        f"--user-data-dir={tmp_profile}",
        url,
    ]
    try:
        subprocess.run(cmd, timeout=60, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    finally:
        srv.shutdown()

    if os.path.exists(out):
        print("已生成:", out, f"({os.path.getsize(out)} bytes)")
        return 0
    print("截图失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
