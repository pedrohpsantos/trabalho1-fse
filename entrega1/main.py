"""Ponto de entrada raiz da Entrega 1 (Python)."""

import os
import sys

# Garante que a pasta entrega1_python está no sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

from src.main import main

if __name__ == "__main__":
    main()
