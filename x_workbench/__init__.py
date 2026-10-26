"""本地 X 资料工作台。"""
import json
from pathlib import Path

# Chrome requires its version in the manifest; all other release surfaces reuse it.
__version__ = json.loads((Path(__file__).resolve().parent.parent/'extension'/'manifest.json').read_text(encoding='utf-8'))['version']
