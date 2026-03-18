"""conftest.py — pytest 全局配置

将 app/ 目录加入 sys.path，使测试文件可以直接 import selector、scraper 等模块，
无需 app. 前缀（与生产代码的 import 风格保持一致）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
