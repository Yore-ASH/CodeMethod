#!/usr/bin/env python
"""CodeMethod 启动脚本.

用法::

    python main.py                 # 空库启动
    python main.py --demo          # 载入示例库
    python main.py mylib.cmdb      # 打开指定代码库
"""

from __future__ import annotations

import os
import sys

# 确保以脚本方式运行时也能 import codemethod 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from codemethod.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
