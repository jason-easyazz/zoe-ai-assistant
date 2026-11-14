"""Make ``zoe_os`` importable for the lab's own tests (the directory name has a hyphen)."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
