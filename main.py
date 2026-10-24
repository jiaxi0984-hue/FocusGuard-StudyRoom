"""入口；源码可使用项目本地依赖，发布包自带运行环境。"""
from pathlib import Path
import sys

local_deps = Path(__file__).parent / "_deps"
if not getattr(sys, "frozen", False) and local_deps.is_dir():
    sys.path.insert(0, str(local_deps))

if __name__ == "__main__":
    from focusguard.ui.app import main
    raise SystemExit(main())
