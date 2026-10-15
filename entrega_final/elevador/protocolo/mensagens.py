"""Protocolo TCP entre Servidor Central e Servidores Distribuídos.

Enquadramento: uma mensagem JSON (UTF-8) por linha ("\\n"), no máximo `MAX_LINHA` bytes.
Todo campo recebido é validado por tipo, faixa e conjunto de campos permitidos.

Cabine -> Central:
  hello        apresentação ao conectar (inclui agenda atual para reconciliação)
  telemetria   estado a cada 500 ms
  evento       embarque / desembarque concluído
  porta_cmd    pedido de comando de porta (o Central é o único dono da UART)
  pedir_modbus pedido de leitura imediata dos registradores da cabine
Central -> Cabine:
  boas_vindas  confirma o hello
  cota         Cota de Potência (validade de 2 s)
  modbus       espelho dos registradores da cabine (andar_atual, porta_estado, ...)
  tarefa       nova viagem atribuída pelo Despacho
  ir_andar     comando manual de envio a um andar
  recalibrar   força nova calibração
"""

import json
from typing import Any

MAX_LINHA = 64 * 1024
VERSAO = 1

_INT = (int,)
_NUM = (int, float)


def _int(minimo: int, maximo: int) -> tuple:
    return ("int", minimo, maximo)


_FAIXA_ANDAR = _int(0, 5)
_FAIXA_CABINE = _int(1, 3)
_FAIXA_U16 = _int(0, 65535)
_FAIXA_ID = _int(0, 2**31 - 1)

# campo -> (tipo, mínimo, máximo) | "str" | "bool" | "num" | "list" | "dict"; "?" prefixo = opcional
ESQUEMAS: dict[str, dict[str, Any]] = {
    "hello": {
        "cabine": _FAIXA_CABINE,
        "token": "str",
        "calibrada": "bool",
        "?andares_mm": "list",
        "?agenda": "list",
    },
    "telemetria": {
        "cabine": _FAIXA_CABINE,
        "ts": "num",
        "pos_mm": "num",
        "andar": _FAIXA_ANDAR,
        "sentido": "str",
        "porta": _int(0, 4),
        "corrente_ma": _FAIXA_U16,
        "estado": "str",
        "degradado": "bool",
        "cota_ma": _FAIXA_U16,
        "necessidade": "str",
        "calibrada": "bool",
        "pendentes": _int(0, 1000),
        "alvo": _int(-1, 5),
        "nivelado": "bool",
        "?falha": "str",
        "?duty": "num",
    },
    "evento": {
        "cabine": _FAIXA_CABINE,
        "tarefa": _FAIXA_ID,
        "tipo": "str",
        "andar": _FAIXA_ANDAR,
        "ts": "num",
    },
    "porta_cmd": {"cabine": _FAIXA_CABINE, "cmd": _int(1, 2)},
    "pedir_modbus": {"cabine": _FAIXA_CABINE},
    "boas_vindas": {"?mensagem": "str"},
    "cota": {"cota_ma": _FAIXA_U16, "ts": "num", "validade_s": "num", "seq": _FAIXA_ID},
    "modbus": {
        "andar_atual": _FAIXA_ANDAR,
        "nivelado": _int(0, 1),
        "porta_estado": _int(0, 4),
        "corrente_ma": _FAIXA_U16,
        "falha": _FAIXA_U16,
        "ts": "num",
    },
    "tarefa": {
        "id": _FAIXA_ID,
        "origem": _FAIXA_ANDAR,
        "destino": _FAIXA_ANDAR,
        "t_registro": "num",
        "?suave": "bool",
    },
    "ir_andar": {"andar": _FAIXA_ANDAR},
    "recalibrar": {},
}

SENTIDOS = {"subindo", "descendo", "parado"}
TIPOS_EVENTO = {"embarque", "desembarque", "manual"}


class MensagemInvalida(ValueError):
    """Mensagem malformada ou fora do esquema."""


def _validar_campo(nome: str, valor: Any, regra: Any) -> None:
    if isinstance(regra, tuple):
        _, minimo, maximo = regra
        if isinstance(valor, bool) or not isinstance(valor, int) or not minimo <= valor <= maximo:
            raise MensagemInvalida(f"campo '{nome}' fora da faixa [{minimo}, {maximo}]")
        return
    validadores = {
        "str": lambda v: isinstance(v, str) and len(v) <= 256,
        "bool": lambda v: isinstance(v, bool),
        "num": lambda v: isinstance(v, _NUM) and not isinstance(v, bool) and v == v,
        "list": lambda v: isinstance(v, list) and len(v) <= 256,
        "dict": lambda v: isinstance(v, dict),
    }
    if not validadores[regra](valor):
        raise MensagemInvalida(f"campo '{nome}' deve ser {regra}")


def validar(msg: Any) -> dict[str, Any]:
    """Valida uma mensagem decodificada; devolve-a ou lança MensagemInvalida."""
    if not isinstance(msg, dict):
        raise MensagemInvalida("mensagem deve ser um objeto JSON")
    tipo = msg.get("t")
    if tipo not in ESQUEMAS:
        raise MensagemInvalida(f"tipo desconhecido: {tipo!r}")
    esquema = ESQUEMAS[tipo]
    permitidos = {"t", "v"} | {c.lstrip("?") for c in esquema}
    extras = set(msg) - permitidos
    if extras:
        raise MensagemInvalida(f"campos não permitidos em '{tipo}': {sorted(extras)}")
    for campo, regra in esquema.items():
        opcional = campo.startswith("?")
        nome = campo.lstrip("?")
        if nome not in msg:
            if opcional:
                continue
            raise MensagemInvalida(f"campo obrigatório ausente em '{tipo}': {nome}")
        _validar_campo(nome, msg[nome], regra)
    if tipo == "telemetria" and msg["sentido"] not in SENTIDOS:
        raise MensagemInvalida("sentido inválido")
    if tipo == "evento" and msg["tipo"] not in TIPOS_EVENTO:
        raise MensagemInvalida("tipo de evento inválido")
    return msg


def codificar(tipo: str, /, **campos: Any) -> bytes:
    """Serializa uma mensagem; valida antes de enviar para evitar protocolo inconsistente."""
    msg = {"t": tipo, "v": VERSAO, **campos}
    validar(msg)
    dados = json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(dados) > MAX_LINHA:
        raise MensagemInvalida("mensagem excede o tamanho máximo")
    return dados


def decodificar(linha: bytes) -> dict[str, Any]:
    """Decodifica e valida uma linha recebida."""
    if len(linha) > MAX_LINHA:
        raise MensagemInvalida("linha excede o tamanho máximo")
    try:
        msg = json.loads(linha.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise MensagemInvalida(f"JSON inválido: {erro}") from erro
    return validar(msg)
