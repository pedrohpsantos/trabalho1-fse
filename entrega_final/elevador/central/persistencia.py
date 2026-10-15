"""Persistência do estado do Central e log de viagens.

* `estado.json`: gravado de forma atômica (arquivo temporário + `os.replace`), contém as
  chamadas em andamento, as posições calibradas conhecidas e contadores; recarregado ao iniciar.
* `viagens.jsonl`: uma linha por viagem concluída (append + fsync), com os instantes de
  registro, embarque e desembarque.
"""

import json
import logging
import os
import threading
from pathlib import Path

from .despacho import Chamada

log = logging.getLogger(__name__)

VERSAO_ESTADO = 1


class Persistencia:
    def __init__(self, pasta: str | Path) -> None:
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.arquivo_estado = self.pasta / "estado.json"
        self.arquivo_viagens = self.pasta / "viagens.jsonl"
        self._trava = threading.Lock()

    def salvar_estado(self, dados: dict) -> None:
        corpo = {"versao": VERSAO_ESTADO, **dados}
        temporario = self.arquivo_estado.with_suffix(".tmp")
        with self._trava:
            with open(temporario, "w", encoding="utf-8") as arq:
                json.dump(corpo, arq, indent=1)
                arq.flush()
                os.fsync(arq.fileno())
            os.replace(temporario, self.arquivo_estado)

    def carregar_estado(self) -> dict | None:
        """Devolve o estado salvo; arquivo ausente ou corrompido => None (parte do zero)."""
        try:
            with open(self.arquivo_estado, encoding="utf-8") as arq:
                dados = json.load(arq)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            log.exception("estado persistido ilegível; ignorando")
            return None
        if not isinstance(dados, dict) or dados.get("versao") != VERSAO_ESTADO:
            log.warning("versão de estado incompatível; ignorando")
            return None
        return dados

    def registrar_viagem(self, chamada: Chamada) -> None:
        linha = json.dumps(
            {
                "id_chamada": chamada.id,
                "origem": chamada.origem,
                "destino": chamada.destino,
                "cabine_atribuida": chamada.cabine,
                "timestamp_registro": chamada.t_registro,
                "timestamp_embarque": chamada.t_embarque,
                "timestamp_desembarque": chamada.t_desembarque,
            }
        )
        with self._trava, open(self.arquivo_viagens, "a", encoding="utf-8") as arq:
            arq.write(linha + "\n")
            arq.flush()
            os.fsync(arq.fileno())

    def ler_viagens(self) -> list[dict]:
        try:
            with open(self.arquivo_viagens, encoding="utf-8") as arq:
                return [json.loads(linha) for linha in arq if linha.strip()]
        except FileNotFoundError:
            return []
