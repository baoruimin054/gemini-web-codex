"""Start the dedicated desktop without opening a console window."""
from pathlib import Path
import subprocess
import sys


root = Path(__file__).resolve().parent
python = Path(sys.executable).with_name('python.exe')
subprocess.Popen([str(python), str(root / 'start.py'), '--desktop'], cwd=root,
                 creationflags=subprocess.CREATE_NO_WINDOW,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
