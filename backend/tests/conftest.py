"""pytest 公共钩子。

本工程的包结构是 backend/app/，pytest 的 cwd 约定也是 backend/，
因此只要把 backend/ 加进 sys.path 即可，不需要额外的安装步骤。
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
