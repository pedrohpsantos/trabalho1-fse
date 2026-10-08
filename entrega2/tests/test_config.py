"""tests/test_config.py - Validação estrita da configuração e da matrícula."""

import pytest

from src.config import validar_e_obter_matricula


def test_matricula_padrao_valida():
    """Garante que a matrícula padrão possui 6 dígitos e retorna bytes crus numéricos."""
    mat_bytes = validar_e_obter_matricula()
    assert len(mat_bytes) == 6
    assert list(mat_bytes) == [6, 5, 4, 3, 2, 1]
    # Garante que nenhum dígito é ASCII (por exemplo, ord('6') é 54)
    for b in mat_bytes:
        assert 0 <= b <= 9
        assert b < 0x30  # 0x30 = '0' em ASCII


def test_matricula_string():
    """Valida conversão de string de dígitos para bytes brutos."""
    mat_bytes = validar_e_obter_matricula("123456")
    assert mat_bytes == bytes([1, 2, 3, 4, 5, 6])


def test_matricula_tamanho_invalido():
    """Rejeita matrículas com tamanho diferente de 6 dígitos."""
    with pytest.raises(ValueError, match="exatamente 6 dígitos"):
        validar_e_obter_matricula([1, 2, 3, 4, 5])

    with pytest.raises(ValueError, match="exatamente 6 dígitos"):
        validar_e_obter_matricula([1, 2, 3, 4, 5, 6, 7])


def test_matricula_caracteres_invalidos():
    """Rejeita caracteres não numéricos ou dígitos fora de 0..9."""
    with pytest.raises(ValueError, match="apenas dígitos numéricos"):
        validar_e_obter_matricula("12345A")

    with pytest.raises(ValueError, match="inteiro entre 0 e 9"):
        validar_e_obter_matricula([1, 2, 3, 4, 5, 10])

    with pytest.raises(ValueError, match="inteiro entre 0 e 9"):
        validar_e_obter_matricula([1, 2, 3, 4, 5, -1])
