"""Configuração de logging padrão dos processos."""

import logging
import sys


def configurar_logs(nome: str, nivel: str = "INFO", arquivo: str | None = None) -> logging.Logger:
    """Configura o logger raiz e devolve o logger do processo."""
    handlers: list[logging.Handler] = []
    if arquivo:
        handlers.append(logging.FileHandler(arquivo, encoding="utf-8"))
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=getattr(logging, nivel.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    return logging.getLogger(nome)
