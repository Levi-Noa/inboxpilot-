# Backend configuration
import sys
from pathlib import Path

# Add parent directory to path so we can import agent
BACKEND_DIR = Path(__file__).parent
ROOT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(ROOT_DIR))
