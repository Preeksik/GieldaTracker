import os
import sys

# Testy uruchamiane z folderu backend: `py -m pytest lab/tests`
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
