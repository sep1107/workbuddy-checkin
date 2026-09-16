"""准备运行环境：在隔离目录创建 venv 并安装 comtypes + pillow。

遵循 WorkBuddy 运行环境隔离约定：所有依赖装在
    <home>/.workbuddy/binaries/python/envs/default
不污染系统 Python。

用法:
    python setup_env.py            # 创建/复用环境并安装依赖
    python setup_env.py --print    # 只打印解释器路径（供其他脚本调用）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
VENV = HOME / ".workbuddy" / "binaries" / "python" / "envs" / "default"
VENV_PY = VENV / "Scripts" / "python.exe"
PKGS = ["comtypes", "pillow"]


def base_python():
    """找一个可用于创建 venv 的基础解释器。"""
    cand = sorted((HOME / ".workbuddy" / "binaries" / "python" / "versions").glob("*/python.exe"))
    if cand:
        return str(cand[-1])
    return sys.executable


def ensure(verbose=True):
    if not VENV_PY.exists():
        if verbose:
            print(f"[setup] 创建 venv: {VENV}")
        subprocess.run([base_python(), "-m", "venv", str(VENV)], check=True)
    if verbose:
        print(f"[setup] 解释器: {VENV_PY}")
        print(f"[setup] 安装依赖: {', '.join(PKGS)}")
    subprocess.run([str(VENV_PY), "-m", "pip", "install", "-q", *PKGS], check=True)

    code = "import comtypes, PIL; print(comtypes.__file__); print(PIL.__version__)"
    out = subprocess.run([str(VENV_PY), "-c", code], capture_output=True, text=True)
    ok = out.returncode == 0
    if verbose:
        print("[setup] 依赖校验:", "OK" if ok else "FAILED")
        if not ok:
            print(out.stdout, out.stderr)
    return {"python": str(VENV_PY), "ok": ok}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只输出 venv 解释器路径")
    a = ap.parse_args()
    if a.print_only:
        print(str(VENV_PY))
    else:
        result = ensure()
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result["ok"] else 1)
