"""
cx2118 Script Weaver v10 - EXE Builder
构建单文件 exe：嵌入 Python + 所有依赖 + 用户协议 + 自动解压
"""
import os, sys, shutil, subprocess, zipfile, urllib.request, json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_VERSION = "3.12.4"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
DIST_DIR = os.path.join(BASE_DIR, "dist")
BUILD_DIR = os.path.join(DIST_DIR, "build_exe")
EMBED_PYTHON_DIR = os.path.join(BUILD_DIR, "python_embed")
LAUNCHER_SRC = os.path.join(BASE_DIR, "launcher_exe.py")
EXE_NAME = "ScriptWeaver.exe"

BUNDLE_FILES = [
    "main.py", "llm_client.py", "pm_agent.py", "workflow_engine.py",
    "plugin_engine.py", "sandbox.py", "storage.py", "multi_file_manager.py",
    "tool_manager.py", "project_dispatch.py", "breakpoint_recovery.py",
    "web_search.py", "index.html", "config.json", "logo.svg",
    "KNOWN_ISSUES.md", "LICENSE", "README.md",
]
BUNDLE_DIRS = ["workspace", "plugins"]

DEPS = ["fastapi==0.109.2", "uvicorn", "openai", "httpx", "sse-starlette==0.10.3"]


def step(msg):
    print(f"\n  -> {msg}")


def ok(msg):
    print(f"     OK  {msg}")


def fail(msg):
    print(f"     FAIL  {msg}")


def download_python():
    step(f"Downloading Python {PYTHON_VERSION} embedded...")
    zip_path = os.path.join(BUILD_DIR, "_python_embed.zip")
    if os.path.exists(os.path.join(EMBED_PYTHON_DIR, "python.exe")):
        ok("Python already downloaded")
        return True
    try:
        urllib.request.urlretrieve(PYTHON_URL, zip_path)
        ok(f"Downloaded ({os.path.getsize(zip_path) // 1024 // 1024} MB)")
    except Exception as e:
        fail(f"Download failed: {e}")
        return False
    os.makedirs(EMBED_PYTHON_DIR, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(EMBED_PYTHON_DIR)
    os.remove(zip_path)
    for f in os.listdir(EMBED_PYTHON_DIR):
        if f.endswith("._pth"):
            p = os.path.join(EMBED_PYTHON_DIR, f)
            with open(p, "r", encoding="utf-8") as fh:
                c = fh.read()
            c = c.replace("#import site", "import site")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(c)
            ok(f"Patched {f}")
    return True


def install_pip():
    step("Installing pip...")
    python_exe = os.path.join(EMBED_PYTHON_DIR, "python.exe")
    if os.path.exists(os.path.join(EMBED_PYTHON_DIR, "Scripts", "pip.exe")):
        ok("pip already installed")
        return True
    get_pip = os.path.join(EMBED_PYTHON_DIR, "get-pip.py")
    try:
        urllib.request.urlretrieve(PIP_URL, get_pip)
    except Exception as e:
        fail(f"Download get-pip.py failed: {e}")
        return False
    r = subprocess.run([python_exe, get_pip, "--no-warn-script-location"],
                       capture_output=True, text=True, cwd=EMBED_PYTHON_DIR)
    if os.path.exists(get_pip):
        os.remove(get_pip)
    if r.returncode != 0:
        fail(f"pip install failed: {r.stderr[-300:]}")
        return False
    ok("pip installed")
    return True


def install_deps():
    step(f"Installing dependencies: {', '.join(DEPS)}...")
    pip_exe = os.path.join(EMBED_PYTHON_DIR, "Scripts", "pip.exe")
    python_exe = os.path.join(EMBED_PYTHON_DIR, "python.exe")
    marker = os.path.join(BUILD_DIR, ".deps_done")
    if os.path.exists(marker):
        ok("Dependencies already installed")
        return True
    r = subprocess.run([pip_exe, "install", "--no-warn-script-location"] + DEPS,
                       capture_output=True, text=True, cwd=EMBED_PYTHON_DIR)
    if r.returncode != 0:
        fail(f"Install failed: {r.stderr[-500:]}")
        return False
    r2 = subprocess.run([python_exe, "-c", "import fastapi,uvicorn,openai,httpx,sse_starlette;print('OK')"],
                        capture_output=True, text=True, cwd=EMBED_PYTHON_DIR)
    if r2.returncode != 0:
        fail(f"Import check failed: {r2.stderr}")
        return False
    with open(marker, "w") as f:
        f.write("done")
    ok("Dependencies installed and verified")
    return True


def create_launcher():
    step("Creating launcher...")
    code = r'''#!/usr/bin/env python3
"""
cx2118 Script Weaver v10 - EXE Launcher
Embedded Python + Auto-extract + License Agreement
"""
import os, sys, subprocess, webbrowser, time, threading, shutil, json

if getattr(sys, 'frozen', False):
    EXE_DIR = os.path.dirname(sys.executable)
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))

APP_DIR = os.path.join(EXE_DIR, "app")
PYTHON_DIR = os.path.join(EXE_DIR, "python")
PYTHON_EXE = os.path.join(PYTHON_DIR, "python.exe")
LICENSE_FILE = os.path.join(EXE_DIR, "LICENSE.txt")
CONFIG_FILE = os.path.join(EXE_DIR, "app", "config.json")

LICENSE_TEXT = """MIT License

Copyright (c) 2026 CX2118

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

cx2118 Script Weaver v10 - AI Multi-Agent Coding Tool
https://github.com/CX2118/CX2118-Script-Weaver
"""


def print_banner():
    print()
    print("  +============================================+")
    print("  |        cx2118 Script Weaver v10            |")
    print("  |        AI Multi-Agent Coding Tool          |")
    print("  +============================================+")
    print()


def show_license():
    if os.path.exists(LICENSE_FILE):
        return True
    print("=" * 50)
    print("  LICENSE AGREEMENT (MIT)")
    print("=" * 50)
    print(LICENSE_TEXT)
    print("=" * 50)
    while True:
        ans = input("\n  Do you accept the license? (yes/no): ").strip().lower()
        if ans in ("yes", "y", "是"):
            with open(LICENSE_FILE, "w", encoding="utf-8") as f:
                f.write("accepted")
            print("  License accepted.")
            return True
        elif ans in ("no", "n", "否"):
            print("  License not accepted. Exiting.")
            return False
        print("  Please type 'yes' or 'no'.")


def extract_app():
    if os.path.exists(os.path.join(APP_DIR, "main.py")):
        return True
    print("  Extracting application files...")
    bundle_dir = getattr(sys, '_MEIPASS', EXE_DIR)
    os.makedirs(APP_DIR, exist_ok=True)
    files = [f for f in os.listdir(bundle_dir) if os.path.isfile(os.path.join(bundle_dir, f))]
    for f in files:
        if f.endswith(('.py', '.html', '.json', '.svg', '.md')):
            shutil.copy2(os.path.join(bundle_dir, f), os.path.join(APP_DIR, f))
    for d in ["workspace", "plugins"]:
        src = os.path.join(bundle_dir, d)
        dst = os.path.join(APP_DIR, d)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copytree(src, dst)
    os.makedirs(os.path.join(APP_DIR, "workspace", "skills", "pending"), exist_ok=True)
    os.makedirs(os.path.join(APP_DIR, "storage", "storage"), exist_ok=True)
    return True


def create_bootstrap():
    bootstrap = os.path.join(APP_DIR, "run.py")
    if os.path.exists(bootstrap):
        return True
    with open(bootstrap, "w", encoding="utf-8") as f:
        f.write("""import os, sys
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
os.chdir(APP_DIR)
exec(compile(open(os.path.join(APP_DIR, "main.py"), encoding="utf-8").read(), "main.py", "exec"))
""")
    return True


def start_server():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = APP_DIR
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [PYTHON_EXE, os.path.join(APP_DIR, "run.py")],
        cwd=APP_DIR, env=env, creationflags=creation_flags,
    )


def open_browser():
    for _ in range(30):
        time.sleep(0.5)
        try:
            import urllib.request
            urllib.request.urlopen("http://localhost:8880", timeout=2)
            break
        except Exception:
            continue
    webbrowser.open("http://localhost:8880")


def main():
    os.system("cls" if os.name == "nt" else "clear")
    print_banner()

    if not show_license():
        input("\n  Press Enter to exit...")
        return

    if not os.path.exists(PYTHON_EXE):
        print("  [ERROR] Embedded Python not found!")
        print("  Please re-download or re-build the package.")
        input("\n  Press Enter to exit...")
        return

    extract_app()
    create_bootstrap()

    print("  Starting server...")
    server = start_server()

    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    print("  Server: http://localhost:8880")
    print("  Browser will open automatically.")
    print()
    print("  Press Ctrl+C or close this window to stop.")
    print()

    try:
        while True:
            time.sleep(1)
            if server.poll() is not None:
                print("\n  Server stopped unexpectedly.")
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
    with open(LAUNCHER_SRC, "w", encoding="utf-8") as f:
        f.write(code)
    ok("launcher_exe.py created")
    return True


def build_exe():
    step("Building EXE with PyInstaller...")
    pyinstaller_exe = os.path.join(EMBED_PYTHON_DIR, "Scripts", "pyinstaller.exe")
    if not os.path.exists(pyinstaller_exe):
        python_exe = os.path.join(EMBED_PYTHON_DIR, "python.exe")
        subprocess.run([python_exe, "-m", "pip", "install", "pyinstaller", "--no-warn-script-location"],
                       capture_output=True, cwd=EMBED_PYTHON_DIR)
        pyinstaller_exe = os.path.join(EMBED_PYTHON_DIR, "Scripts", "pyinstaller.exe")

    cmd = [
        pyinstaller_exe,
        "--onefile",
        "--name", "ScriptWeaver",
        "--clean",
        "--noconfirm",
        "--distpath", DIST_DIR,
        "--workpath", os.path.join(BUILD_DIR, "build"),
        "--specpath", BUILD_DIR,
        LAUNCHER_SRC,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=BASE_DIR)
    if r.returncode != 0:
        fail(f"PyInstaller failed:\n{r.stdout[-1000:]}\n{r.stderr[-1000:]}")
        return False
    ok("EXE built successfully")
    return True


def create_package():
    step("Creating release package...")
    pkg_dir = os.path.join(DIST_DIR, "ScriptWeaver-EXE")
    exe_path = os.path.join(DIST_DIR, "ScriptWeaver.exe")
    if not os.path.exists(exe_path):
        fail("ScriptWeaver.exe not found")
        return False
    if os.path.exists(pkg_dir):
        shutil.rmtree(pkg_dir)
    os.makedirs(pkg_dir)

    shutil.copy2(exe_path, os.path.join(pkg_dir, "ScriptWeaver.exe"))

    python_pkg = os.path.join(pkg_dir, "python")
    shutil.copytree(EMBED_PYTHON_DIR, python_pkg)

    app_pkg = os.path.join(pkg_dir, "app")
    os.makedirs(app_pkg, exist_ok=True)
    for f in BUNDLE_FILES:
        src = os.path.join(BASE_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(app_pkg, f))
    for d in BUNDLE_DIRS:
        src = os.path.join(BASE_DIR, d)
        if os.path.exists(src):
            shutil.copytree(src, os.path.join(app_pkg, d), dirs_exist_ok=True)
    os.makedirs(os.path.join(app_pkg, "workspace", "skills", "pending"), exist_ok=True)
    os.makedirs(os.path.join(app_pkg, "storage", "storage"), exist_ok=True)

    with open(os.path.join(pkg_dir, "README.txt"), "w", encoding="utf-8") as f:
        f.write("""cx2118 Script Weaver v10 - EXE Edition
======================================

How to use:
  1. Double-click ScriptWeaver.exe
  2. Accept the MIT license
  3. Browser opens automatically
  4. Start coding!

No Python installation needed.
Everything is self-contained in this folder.

License: MIT
https://github.com/CX2118/CX2118-Script-Weaver
""")

    ok(f"Package created: {pkg_dir}")
    return True


def sign_exe():
    step("Creating self-signed certificate...")
    pkg_dir = os.path.join(DIST_DIR, "ScriptWeaver-EXE")
    exe_path = os.path.join(pkg_dir, "ScriptWeaver.exe")
    pfx_path = os.path.join(pkg_dir, "cx2118.pfx")
    cert_path = os.path.join(DIST_DIR, "cx2118_cert.pem")
    key_path = os.path.join(DIST_DIR, "cx2118_key.pem")

    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_path, "-out", cert_path,
            "-days", "3650", "-nodes",
            "-subj", "/CN=CX2118/O=CX2118/C=CN",
        ], capture_output=True, check=True)

        subprocess.run([
            "openssl", "pkcs12", "-export",
            "-out", pfx_path, "-keyfile", key_path,
            "-certfile", cert_path, "-passout", "pass:cx2118",
        ], capture_output=True, check=True)

        signtool = r"C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
        if not os.path.exists(signtool):
            import glob
            candidates = glob.glob(r"C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe")
            signtool = candidates[-1] if candidates else None

        if signtool and os.path.exists(pfx_path):
            r = subprocess.run([
                signtool, "sign", "/f", pfx_path,
                "/p", "cx2118",
                "/t", "http://timestamp.digicert.com",
                exe_path,
            ], capture_output=True, text=True)
            if r.returncode == 0:
                ok("EXE signed successfully")
            else:
                print(f"     WARN  Signing failed (non-fatal): {r.stderr[:200]}")
        else:
            print("     WARN  signtool.exe not found, skipping Windows signing")
            print("     INFO  Certificate saved for manual signing")

        for f in [cert_path, key_path]:
            if os.path.exists(f):
                os.remove(f)

        return True
    except FileNotFoundError:
        print("     WARN  OpenSSL not found, skipping certificate creation")
        return True
    except Exception as e:
        print(f"     WARN  Signing error: {e}")
        return True


def summary():
    pkg_dir = os.path.join(DIST_DIR, "ScriptWeaver-EXE")
    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fnames in os.walk(pkg_dir) for f in fnames)
    print(f"\n  Build complete!")
    print(f"  Package: {pkg_dir}")
    print(f"  Size:    ~{total // 1024 // 1024} MB")
    print(f"\n  Distribution:")
    print(f"  1. Zip the folder: dist/ScriptWeaver-EXE/")
    print(f"  2. Users double-click ScriptWeaver.exe")
    print(f"  3. Accept license -> Server starts -> Browser opens")
    print()


if __name__ == "__main__":
    print("\n  cx2118 Script Weaver v10 - EXE Builder")
    print("  This builds a single-file EXE with embedded Python.\n")

    try:
        os.makedirs(BUILD_DIR, exist_ok=True)
        os.makedirs(DIST_DIR, exist_ok=True)

        if not download_python():
            sys.exit(1)
        if not install_pip():
            sys.exit(1)
        if not install_deps():
            sys.exit(1)
        if not create_launcher():
            sys.exit(1)
        if not build_exe():
            sys.exit(1)
        if not create_package():
            sys.exit(1)
        sign_exe()
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
