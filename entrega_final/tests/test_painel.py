"""Painel de terminal do Central: faixa fixa no topo sem apagar o que o operador digita."""

import io
import os
import threading

from elevador.central.painel import ALTURA_PAINEL, ESC, Painel, VisaoPainel, renderizar


def visao_exemplo(pendentes=None) -> VisaoPainel:
    cabine = {
        "numero": 1,
        "conectada": True,
        "estado": "ociosa",
        "andar": 0,
        "pos_mm": 0.0,
        "sentido": "parado",
        "porta": 0,
        "corrente_ma": 300,
        "cota_ma": 300,
        "degradado": False,
    }
    return VisaoPainel(
        cabines=[cabine, {**cabine, "numero": 2}, {**cabine, "numero": 3, "conectada": False}],
        barramento_max_ma=3000,
        watchdog=1,
        temperatura_c=None,
        pressao_hpa=None,
        cenario=0,
        geradas=0,
        atendidas=0,
        espera_s=0.0,
        viagem_s=0.0,
        pendentes=pendentes or [],
        em_atendimento=[],
        erros_modbus=0,
    )


class SaidaFalsa(io.StringIO):
    def isatty(self) -> bool:
        return True


def criar(respostas=None, colunas=60, linhas=40):
    saida = SaidaFalsa()
    recebidos = []

    def comando(linha):
        recebidos.append(linha)
        return (respostas or {}).get(linha, "")

    painel = Painel(
        lambda: visao_exemplo(),
        comando,
        threading.Event(),
        interativo=True,
        saida=saida,
        tamanho=lambda: os.terminal_size((colunas, linhas)),
    )
    return painel, saida, recebidos


def test_painel_cabe_na_faixa_reservada():
    linhas = renderizar(visao_exemplo()).split("\n")
    assert len(linhas) <= ALTURA_PAINEL - 1  # sobra a linha do separador


def test_tela_preparada_com_regiao_de_rolagem_para_os_comandos():
    painel, saida, _ = criar(linhas=40)
    painel._preparar_tela()
    texto = saida.getvalue()
    assert f"{ESC}[{ALTURA_PAINEL + 1};40r" in texto  # comandos rolam só abaixo do painel
    assert texto.endswith("> ")


def test_redesenho_nao_limpa_a_tela_e_preserva_o_cursor_do_operador():
    painel, saida, _ = criar(colunas=60)
    quadro = painel._quadro(renderizar(visao_exemplo(pendentes=[f"#{i}(0->5)→C1" for i in range(40)])))
    assert quadro.startswith(f"{ESC}7") and quadro.endswith(f"{ESC}8")  # salva/restaura o cursor
    assert f"{ESC}[2J" not in quadro  # nunca apaga a tela inteira (apagaria o que está sendo digitado)
    for i in range(1, ALTURA_PAINEL + 1):
        assert f"{ESC}[{i};1H" in quadro  # escreve só nas linhas do painel
    conteudos = [parte.split(f"{ESC}[K")[0] for parte in quadro.split(";1H")[1:]]
    assert all(len(c) <= 59 for c in conteudos)  # linhas longas cortadas: não invadem a área de comandos


def test_comando_digitado_e_executado_e_a_resposta_aparece_com_novo_prompt():
    painel, saida, recebidos = criar(respostas={"ir 1 5": "cabine 1 -> andar 5"})
    painel.executar("ir 1 5\n")
    assert recebidos == ["ir 1 5"]
    assert saida.getvalue().endswith("cabine 1 -> andar 5\n> ")


def test_encerrar_devolve_o_terminal_ao_normal():
    painel, saida, _ = criar(linhas=30)
    painel.encerrar()
    assert f"{ESC}[r" in saida.getvalue()  # rolagem da tela inteira restaurada
