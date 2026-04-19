"""News Radar · Playwright 啟動診斷腳本

用法：
    .venv/bin/python diagnose_playwright.py

目標：查出 competitor_agent.py --diagnose 卡在 [0/2] 的真正原因。
階段：
  A. 檢查 node driver 執行檔是否存在、大小是否正常
  B. 直接 subprocess 跑 `node --version`（不經過 Playwright）
     → 卡 >10s 代表 OneDrive 鎖住二進位檔 / Gatekeeper 問題
  C. 嘗試用 Playwright 的低階 API 啟動 driver
"""
import os
import sys
import time
import subprocess
from pathlib import Path


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def main() -> None:
    section("A. 環境資訊")
    print("python:", sys.executable)
    print("version:", sys.version.split()[0])
    print("cwd:", os.getcwd())

    try:
        import playwright  # type: ignore
    except Exception as e:
        print(f"❌ import playwright 失敗：{e}")
        sys.exit(1)
    print("playwright package:", playwright.__file__)

    driver_dir = Path(playwright.__file__).parent / "driver"
    print("driver dir:", driver_dir)
    if not driver_dir.exists():
        print("❌ driver 目錄不存在。請先跑：playwright install")
        sys.exit(1)
    print("contents:", sorted(os.listdir(driver_dir)))

    node_bin = driver_dir / "node"
    print("node binary:", node_bin)
    print("exists:", node_bin.exists())
    if node_bin.exists():
        st = node_bin.stat()
        print("size bytes:", st.st_size)
        print("mode:", oct(st.st_mode))
        # OneDrive 的雲端預留檔通常大小 >0 但內容未下載，會在讀取時延遲下載
        if st.st_size < 1_000_000:
            print("⚠️ 檔案小於 1MB，可能是 OneDrive 尚未下載的預留檔。")

    section("B. 直接跑 node --version（10s 上限）")
    if not node_bin.exists():
        print("跳過：node binary 不存在")
    else:
        t0 = time.time()
        try:
            r = subprocess.run(
                [str(node_bin), "--version"],
                capture_output=True,
                timeout=10,
                text=True,
            )
            elapsed = time.time() - t0
            print(f"elapsed: {elapsed:.2f}s")
            print("returncode:", r.returncode)
            print("stdout:", repr(r.stdout))
            print("stderr:", repr(r.stderr))
            if elapsed > 3:
                print("⚠️ 首次執行耗時偏長，可能是 OneDrive 首次下載或 Gatekeeper 驗證")
        except subprocess.TimeoutExpired:
            print("❌ 超過 10s：node driver 執行被卡住")
            print("   最可能原因：venv 在 OneDrive 內，雲端同步 / Gatekeeper 鎖住二進位檔")
            print("   解法：把 venv 建到 OneDrive 外（例如 ~/.virtualenvs/news_radar）")
            sys.exit(2)
        except Exception as e:
            print(f"❌ 失敗：{e}")
            sys.exit(3)

    section("C. Playwright 驅動握手（sync API，15s 上限）")
    # 用 sync API 避免 asyncio context manager 的複雜度
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:
        print(f"❌ import sync_playwright 失敗：{e}")
        sys.exit(4)

    t0 = time.time()
    try:
        # sync_playwright 也是 context manager；進入時啟動 driver
        with sync_playwright() as p:
            elapsed = time.time() - t0
            print(f"driver handshake 成功，耗時 {elapsed:.2f}s")
            print("chromium executable:", p.chromium.executable_path)
            print("firefox  executable:", p.firefox.executable_path)
        print("✅ 通過：Playwright driver 可正常握手")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"❌ 失敗（{elapsed:.2f}s）：{e}")
        sys.exit(5)


if __name__ == "__main__":
    main()
