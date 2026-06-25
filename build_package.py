"""
cx2118 Script Weaver v10 - Package Builder
构建便携式发布包：嵌入式Python + 一键启动
"""

import os
import sys
import subprocess
import shutil
import zipfile
import urllib.request
import time

# ═══════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════

PYTHON_VERSION = "3.12.4"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PIP_BOOTSTRAP_URL = "https://bootstrap.pypa.io/get-pip.py"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "dist", "ScriptWeaver")
PYTHON_DIR = os.path.join(DIST_DIR, "python")
APP_DIR = os.path.join(DIST_DIR, "app")

BUNDLE_PYTHON_FILES = [
    "main.py", "llm_client.py", "pm_agent.py", "workflow_engine.py",
    "plugin_engine.py", "sandbox.py", "storage.py", "multi_file_manager.py",
    "tool_manager.py", "project_dispatch.py", "breakpoint_recovery.py",
    "web_search.py", "index.html", "config.json", "logo.svg",
    "KNOWN_ISSUES.md", "LICENSE", "README.md",
]

BUNDLE_DIRS = ["workspace", "plugins"]

DEPS = ["fastapi==0.109.2", "uvicorn", "openai", "httpx", "sse-starlette==0.10.3"]


def banner(msg):
    print(f"\n{'='*50}")
    print(f"  {msg}")
    print(f"{'='*50}")


def step(msg):
    print(f"\n  -> {msg}")


def ok(msg):
    print(f"     OK  {msg}")


def fail(msg):
    print(f"     FAIL  {msg}")


# ═══════════════════════════════════════════════════════
# Step 1: 清理旧的构建
# ═══════════════════════════════════════════════════════

def clean():
    banner("Step 1/6 - Clean")
    if os.path.exists(DIST_DIR):
        shutil.rmtree(DIST_DIR)
        ok("Old dist cleaned")
    os.makedirs(DIST_DIR, exist_ok=True)
    os.makedirs(PYTHON_DIR, exist_ok=True)
    os.makedirs(APP_DIR, exist_ok=True)
    ok("Directory structure created")


# ═══════════════════════════════════════════════════════
# Step 2: 下载嵌入式 Python
# ═══════════════════════════════════════════════════════

def download_python():
    banner("Step 2/6 - Download Embedded Python")

    zip_path = os.path.join(BASE_DIR, "_python_embed.zip")

    step(f"Downloading Python {PYTHON_VERSION} embedded...")
    try:
        urllib.request.urlretrieve(PYTHON_EMBED_URL, zip_path)
        ok(f"Downloaded ({os.path.getsize(zip_path) // 1024 // 1024} MB)")
    except Exception as e:
        fail(f"Download failed: {e}")
        fail("Please check your network connection")
        return False

    step("Extracting...")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(PYTHON_DIR)
    os.remove(zip_path)
    ok("Extracted to python/")

    step("Enabling site-packages in embedded Python...")
    pth_files = [f for f in os.listdir(PYTHON_DIR) if f.endswith("._pth")]
    for pth_file in pth_files:
        pth_path = os.path.join(PYTHON_DIR, pth_file)
        with open(pth_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("#import site", "import site")
        with open(pth_path, "w", encoding="utf-8") as f:
            f.write(content)
        ok(f"Patched {pth_file}")

    return True


# ═══════════════════════════════════════════════════════
# Step 3: 安装 pip
# ═══════════════════════════════════════════════════════

def install_pip():
    banner("Step 3/6 - Install pip")

    python_exe = os.path.join(PYTHON_DIR, "python.exe")
    get_pip_path = os.path.join(PYTHON_DIR, "get-pip.py")

    step("Downloading get-pip.py...")
    try:
        urllib.request.urlretrieve(PIP_BOOTSTRAP_URL, get_pip_path)
    except Exception as e:
        fail(f"Download failed: {e}")
        return False

    step("Installing pip...")
    result = subprocess.run(
        [python_exe, get_pip_path, "--no-warn-script-location"],
        capture_output=True, text=True, cwd=PYTHON_DIR
    )
    if get_pip_path and os.path.exists(get_pip_path):
        os.remove(get_pip_path)

    if result.returncode != 0:
        fail(f"pip install failed:\n{result.stderr}")
        return False

    ok("pip installed")
    return True


# ═══════════════════════════════════════════════════════
# Step 4: 安装依赖包
# ═══════════════════════════════════════════════════════

def install_deps():
    banner("Step 4/6 - Install Dependencies")

    python_exe = os.path.join(PYTHON_DIR, "python.exe")
    pip_exe = os.path.join(PYTHON_DIR, "Scripts", "pip.exe")

    step(f"Installing: {', '.join(DEPS)}")
    result = subprocess.run(
        [pip_exe, "install", "--no-warn-script-location"] + DEPS,
        capture_output=True, text=True, cwd=PYTHON_DIR
    )

    if result.returncode != 0:
        fail(f"Install failed:\n{result.stderr[-500:]}")
        return False

    ok("All dependencies installed")

    step("Verifying imports...")
    verify_result = subprocess.run(
        [python_exe, "-c",
         "import fastapi, uvicorn, openai, httpx, sse_starlette; print('All imports OK')"],
        capture_output=True, text=True, cwd=PYTHON_DIR
    )
    if verify_result.returncode == 0:
        ok(verify_result.stdout.strip())
    else:
        fail(f"Import verification failed:\n{verify_result.stderr}")
        return False

    return True


# ═══════════════════════════════════════════════════════
# Step 5: 复制源文件
# ═══════════════════════════════════════════════════════

def copy_source():
    banner("Step 5/6 - Copy Source Files")

    step("Copying Python/HTML/Config files...")
    for f in BUNDLE_PYTHON_FILES:
        src = os.path.join(BASE_DIR, f)
        dst = os.path.join(APP_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            ok(f"  {f}")
        else:
            fail(f"  {f} not found, skipping")

    for d in BUNDLE_DIRS:
        src = os.path.join(BASE_DIR, d)
        dst = os.path.join(APP_DIR, d)
        if os.path.exists(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            ok(f"  {d}/")
        else:
            fail(f"  {d}/ not found, skipping")

    ok("Source files copied")


# ═══════════════════════════════════════════════════════
# Step 6: 创建启动器
# ═══════════════════════════════════════════════════════

def create_launcher():
    banner("Step 6/6 - Create Launcher")

    launcher_py = os.path.join(DIST_DIR, "ScriptWeaver.py")

    launcher_code = '''#!/usr/bin/env python3
"""
cx2118 Script Weaver v10 - Launcher
One-click start, zero system impact.
"""
import os, sys, subprocess, webbrowser, time, threading, signal

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.join(BASE_DIR, "python")
APP_DIR = os.path.join(BASE_DIR, "app")
PYTHON_EXE = os.path.join(PYTHON_DIR, "python.exe")
MAIN_PY = os.path.join(APP_DIR, "main.py")

def cls():
    os.system("cls" if os.name == "nt" else "clear")

def banner():
    print()
    print("  +============================================+")
    print("  |        cx2118 Script Weaver v10            |")
    print("  |        AI Multi-Agent Coding Tool          |")
    print("  +============================================+")
    print()

def check_env():
    if not os.path.exists(PYTHON_EXE):
        print("  [ERROR] python/ not found!")
        print("  Please make sure the python/ folder exists.")
        input("\\n  Press Enter to exit...")
        sys.exit(1)
    if not os.path.exists(MAIN_PY):
        print("  [ERROR] app/ not found!")
        print("  Please make sure the app/ folder exists.")
        input("\\n  Press Enter to exit...")
        sys.exit(1)

def start_server():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = APP_DIR

    process = subprocess.Popen(
        [PYTHON_EXE, MAIN_PY],
        cwd=APP_DIR,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return process

def open_browser():
    for i in range(30):
        time.sleep(0.5)
        try:
            import urllib.request
            urllib.request.urlopen("http://localhost:8880", timeout=2)
            break
        except Exception:
            continue
    webbrowser.open("http://localhost:8880")

def main():
    cls()
    banner()
    check_env()

    print("  Starting server...")
    print("  Browser will open automatically.")
    print()

    server = start_server()

    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    print("  Server: http://localhost:8880")
    print()
    print("  Press Ctrl+C or close this window to stop.")
    print()

    try:
        while True:
            time.sleep(1)
            if server.poll() is not None:
                print("\\n  Server stopped unexpectedly.")
                break
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.terminate()
            server.wait(timeout=5)
        except Exception:
            server.kill()
        print("  Server stopped. Goodbye!")

if __name__ == "__main__":
    main()
'''

    with open(launcher_py, "w", encoding="utf-8") as f:
        f.write(launcher_code)
    ok("ScriptWeaver.py created")

    bat_path = os.path.join(DIST_DIR, "Start.bat")
    bat_content = f'''@echo off
title cx2118 Script Weaver v10
cd /d "%~dp0"
"{os.path.join(PYTHON_DIR, "python.exe")}" ScriptWeaver.py
pause
'''
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)
    ok("Start.bat created")

    readme_path = os.path.join(DIST_DIR, "README.txt")
    readme_content = """cx2118 Script Weaver v10 - Portable Edition
=============================================

How to use:
  1. Double-click "Start.bat" (or run ScriptWeaver.py)
  2. Wait for the browser to open automatically
  3. Start coding!

Requirements:
  - Windows 10/11
  - Internet connection (first time only, for API calls)
  - No Python installation needed!

Files:
  python/   - Embedded Python (portable, no system install)
  app/      - Application source files
  Start.bat - One-click launcher

API Key Setup:
  Open the web page, go to Settings, and enter your API key.

License: MIT
'''
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_content)
    ok("README.txt created")

    return True


def summary():
    banner("Build Complete!")
    total_size = 0
    for root, dirs, files in os.walk(DIST_DIR):
        for f in files:
            total_size += os.path.getsize(os.path.join(root, f))
    size_mb = total_size // 1024 // 1024

    print(f"  Output: {DIST_DIR}")
    print(f"  Size:   ~{size_mb} MB")
    print()
    print("  To distribute:")
    print(f"  1. Zip the folder: dist/ScriptWeaver/")
    print(f"  2. Users just extract and double-click Start.bat")
    print()
    print("  Folder contents:")
    for item in sorted(os.listdir(DIST_DIR)):
        full = os.path.join(DIST_DIR, item)
        if os.path.isdir(full):
            print(f"    {item}/")
        else:
            size = os.path.getsize(full)
            print(f"    {item}  ({size//1024}KB)")
    print()


if __name__ == "__main__":
    print("\n  cx2118 Script Weaver v10 - Package Builder")
    print("  This will download Python and build a portable package.")
    print("  It may take a few minutes on first run.\n")
    input("  Press Enter to start...")

    try:
        clean()
        if not download_python():
            sys.exit(1)
        if not install_pip():
            sys.exit(1)
        if not install_deps():
            sys.exit(1)
        copy_source()
        if not create_launcher():
            sys.exit(1)
        summary()
    except KeyboardInterrupt:
        print("\n\n  Build cancelled.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n  Build failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    input("  Press Enter to exit...")
