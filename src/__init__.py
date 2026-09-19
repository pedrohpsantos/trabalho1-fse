"""Sistema de Controle de Elevadores - FSE 2026/2 - Entrega 1 (Cabine 1)."""

import sys

# Garante suporte seguro a UTF-8 em terminais Windows e redirecionamentos
if hasattr(sys.stdout, "reconfigure"):
    try:
        if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
