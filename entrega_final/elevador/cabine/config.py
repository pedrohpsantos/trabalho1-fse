"""Configuração de uma cabine, carregada de arquivo JSON (pinos, endereço MODBUS, TCP, PID)."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..constantes import ENDERECOS_CABINES


@dataclass(frozen=True)
class Pinos:
    pwm: int
    dir1: int
    dir2: int
    enc_a: int
    enc_b: int
    cortina: int
    sensor_andar: int


# Tabela 1 do enunciado (colunas das Cabines 1 e 2 já na ordem corrigida).
PINOS_OFICIAIS = {
    1: Pinos(pwm=13, dir1=22, dir2=23, enc_a=20, enc_b=21, cortina=26, sensor_andar=0),
    2: Pinos(pwm=11, dir1=17, dir2=27, enc_a=5, enc_b=6, cortina=16, sensor_andar=12),
    3: Pinos(pwm=18, dir1=24, dir2=25, enc_a=7, enc_b=8, cortina=19, sensor_andar=1),
}


@dataclass(frozen=True)
class ParametrosPid:
    kp: float = 0.5
    ki: float = 0.05
    kd: float = 40.0
    limite_integral_pct: float = 15.0
    rampa_pct_por_s: float = 25.0


@dataclass(frozen=True)
class ParametrosCabine:
    tempo_porta_aberta_s: float = 4.0
    duty_calibracao: float = 40.0
    margem_extremo_mm: float = 60.0
    debounce_cortina_s: float = 0.04


@dataclass(frozen=True)
class ConfigCabine:
    cabine: int
    endereco_modbus: int
    central_host: str
    central_porta: int
    pinos: Pinos
    token: str = ""
    pid: ParametrosPid = field(default_factory=ParametrosPid)
    params: ParametrosCabine = field(default_factory=ParametrosCabine)


def _inteiro(valor: object) -> int:
    return int(valor, 0) if isinstance(valor, str) else int(valor)


def carregar_config(caminho: str | Path) -> ConfigCabine:
    """Lê e valida o arquivo; qualquer inconsistência lança ValueError."""
    with open(caminho, encoding="utf-8") as arq:
        bruto = json.load(arq)
    try:
        cabine = _inteiro(bruto["cabine"])
        if cabine not in ENDERECOS_CABINES:
            raise ValueError(f"cabine deve ser 1, 2 ou 3 (recebido {cabine})")
        endereco = _inteiro(bruto.get("endereco_modbus", ENDERECOS_CABINES[cabine]))
        if endereco != ENDERECOS_CABINES[cabine]:
            raise ValueError(f"endereço MODBUS 0x{endereco:02X} não corresponde à cabine {cabine}")
        if "pinos" in bruto:
            pinos = Pinos(**{k: _inteiro(v) for k, v in bruto["pinos"].items()})
        else:
            pinos = PINOS_OFICIAIS[cabine]
        central = bruto["central"]
        porta = _inteiro(central["porta"])
        if not 1 <= porta <= 65535:
            raise ValueError("porta TCP inválida")
        return ConfigCabine(
            cabine=cabine,
            endereco_modbus=endereco,
            central_host=str(central["host"]),
            central_porta=porta,
            pinos=pinos,
            token=str(central.get("token", "")),
            pid=ParametrosPid(**bruto.get("pid", {})),
            params=ParametrosCabine(**bruto.get("params", {})),
        )
    except (KeyError, TypeError) as erro:
        raise ValueError(f"configuração inválida em {caminho}: {erro!r}") from erro
