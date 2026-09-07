import sys
from pathlib import Path

# Ensure workspace root is always in sys.path regardless of how pytest is invoked
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
