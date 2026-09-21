"""Suíte de testes automatizados para controle, perfil de movimento, física e sensores."""

import os
import sys
import time
import unittest

# Adiciona o diretório da entrega1_python ao sys.path para importações portáveis
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_dir = os.path.dirname(_current_dir)
if _project_dir not in sys.path:
    sys.path.insert(0, _project_dir)

from src.config import PinConfig, MotorDirection, Fisica
from src.controller.motion import (
    MotionPlanner,
    EstadoParado,
    ContinuarUpdate,
    ChegouAoAndarUpdate,
    LimiteCursoAtingidoUpdate,
)
from src.sensors.bandeirola import BandeirolaTracker
from src.hal.mock import MockHardware
from src.hal.base import CortinaEvent, QUADRATURE_TABLE


class TestBandeirolaTracker(unittest.TestCase):
    def test_bandeirola_centro_e_erro(self):
        tracker = BandeirolaTracker()

        # Simula entrada da bandeirola do Andar 1 (nominal: 3000 mm) em 2850 mm
        report_in = tracker.on_edge(True, 2850)
        self.assertIsNone(report_in)

        # Simula saída da bandeirola em 3170 mm
        report_out = tracker.on_edge(False, 3170)
        self.assertIsNotNone(report_out)

        self.assertEqual(report_out.andar_nominal, 1)
        self.assertEqual(report_out.pos_nominal, 3000)
        self.assertEqual(report_out.pos_entrada, 2850)
        self.assertEqual(report_out.pos_saida, 3170)
        self.assertEqual(report_out.largura, 320)
        self.assertEqual(report_out.centro_estimado, 3010)
        self.assertEqual(report_out.erro_mm, 10)

        # Verifica formatação de string sem exceções
        display = report_out.format_display()
        self.assertIn("[BANDEIROLA]", display)
        self.assertIn("3010 mm", display)


class TestMotionPlanner(unittest.TestCase):
    def test_motion_rampa_e_nivelamento(self):
        planner = MotionPlanner()

        # Comanda ir para o Andar 1 (3000 mm) partindo de 0 mm
        planner.comandar_andar(1, 3000)

        # Na partida: duty deve começar no piso de arranque (20% >= 10%)
        update = planner.update(0)
        self.assertIsInstance(update, ContinuarUpdate)
        self.assertEqual(update.direcao, MotorDirection.SUBIR)
        self.assertGreaterEqual(update.duty_percent, Fisica.MIN_DUTY_ARRANQUE)

        # Acelerando no meio do curso (ex: 0 a 2000 mm): deve atingir velocidade de cruzeiro
        pos = 0
        while pos < 2000:
            planner.update(pos)
            pos += 100

        update = planner.update(2000)
        self.assertIsInstance(update, ContinuarUpdate)
        self.assertEqual(update.direcao, MotorDirection.SUBIR)
        self.assertEqual(update.duty_percent, Fisica.CRUISE_DUTY)

        # Na aproximação (ex: 2900 mm): deve desacelerar para valor menor que o cruzeiro
        update = planner.update(2900)
        self.assertIsInstance(update, ContinuarUpdate)
        self.assertEqual(update.direcao, MotorDirection.SUBIR)
        self.assertLess(update.duty_percent, Fisica.CRUISE_DUTY)

        # Na tolerância de nivelamento (ex: 2995 mm, erro de 5 mm <= 10 mm): deve parar e frear
        update = planner.update(2995)
        self.assertIsInstance(update, ChegouAoAndarUpdate)
        self.assertEqual(update.andar, 1)
        self.assertEqual(update.posicao, 2995)
        self.assertEqual(update.erro_nivelamento, 5)

        self.assertIsInstance(planner.estado, EstadoParado)

    def test_protecao_fim_de_curso(self):
        planner = MotionPlanner()

        # Movimento manual para subir além de 6000 mm
        planner.comandar_manual(MotorDirection.SUBIR, 50.0)

        # Em 5900 mm: deve continuar
        update = planner.update(5900)
        self.assertIsInstance(update, ContinuarUpdate)

        # Em 6050 mm: limite de curso atingido
        update = planner.update(6050)
        self.assertIsInstance(update, LimiteCursoAtingidoUpdate)
        self.assertEqual(update.posicao, 6050)
        self.assertIsInstance(planner.estado, EstadoParado)

        # Movimento manual para descer abaixo de 0 mm
        planner.comandar_manual(MotorDirection.DESCER, 40.0)
        update = planner.update(100)
        self.assertIsInstance(update, ContinuarUpdate)

        update = planner.update(-10)
        self.assertIsInstance(update, LimiteCursoAtingidoUpdate)
        self.assertEqual(update.posicao, -10)
        self.assertIsInstance(planner.estado, EstadoParado)


class TestConfigEHardware(unittest.TestCase):
    def test_direcao_motor_tabela_gpio(self):
        self.assertEqual(MotorDirection.LIVRE.to_gpio_levels(), (False, False))
        self.assertEqual(MotorDirection.SUBIR.to_gpio_levels(), (True, False))
        self.assertEqual(MotorDirection.DESCER.to_gpio_levels(), (False, True))
        self.assertEqual(MotorDirection.FREIO.to_gpio_levels(), (True, True))

    def test_pin_config_presets(self):
        tabela = PinConfig.tabela_oficial()
        self.assertEqual(tabela.pwm, 13)
        self.assertEqual(tabela.dir1, 22)
        self.assertEqual(tabela.dir2, 23)
        self.assertEqual(tabela.enc_a, 20)
        self.assertEqual(tabela.enc_b, 21)
        self.assertEqual(tabela.cortina, 26)
        self.assertEqual(tabela.sensor_andar, 0)

        bancada = PinConfig.widget_bancada()
        self.assertEqual(bancada.dir1, 17)
        self.assertEqual(bancada.dir2, 27)
        self.assertEqual(bancada.sensor_andar, 11)

    def test_decodificacao_quadratura(self):
        # Sequência de subida (+1): 00 -> 01 -> 11 -> 10 -> 00
        step1 = QUADRATURE_TABLE[((0b00 << 2) | 0b01)]
        step2 = QUADRATURE_TABLE[((0b01 << 2) | 0b11)]
        step3 = QUADRATURE_TABLE[((0b11 << 2) | 0b10)]
        step4 = QUADRATURE_TABLE[((0b10 << 2) | 0b00)]

        self.assertEqual(step1, 1)
        self.assertEqual(step2, 1)
        self.assertEqual(step3, 1)
        self.assertEqual(step4, 1)

        # Sequência de descida (-1): 00 -> 10 -> 11 -> 01 -> 00
        rev1 = QUADRATURE_TABLE[((0b00 << 2) | 0b10)]
        rev2 = QUADRATURE_TABLE[((0b10 << 2) | 0b11)]
        rev3 = QUADRATURE_TABLE[((0b11 << 2) | 0b01)]
        rev4 = QUADRATURE_TABLE[((0b01 << 2) | 0b00)]

        self.assertEqual(rev1, -1)
        self.assertEqual(rev2, -1)
        self.assertEqual(rev3, -1)
        self.assertEqual(rev4, -1)

    def test_mock_hardware_fisica(self):
        mock = MockHardware(initial_position=0)
        self.assertEqual(mock.get_position(), 0)

        # Atrito estático: duty abaixo de 10% (ex: 5%) não deve mover
        mock.set_motor(MotorDirection.SUBIR, 5.0)
        time.sleep(0.06)
        self.assertEqual(mock.get_position(), 0)

        # Superando atrito estático: duty de 50% deve subir
        mock.set_motor(MotorDirection.SUBIR, 50.0)
        time.sleep(0.12)
        pos = mock.get_position()
        self.assertGreater(pos, 0, f"Deveria ter subido, posição: {pos}")

        # Freio: deve parar
        mock.set_motor(MotorDirection.FREIO, 0.0)
        pos_freado = mock.get_position()
        time.sleep(0.06)
        self.assertEqual(mock.get_position(), pos_freado)

        # Teste de evento manual da cortina
        mock.trigger_curtain(True)
        self.assertTrue(mock.is_curtain_obstructed())
        event = mock.get_event_queue().get(timeout=0.2)
        self.assertIsInstance(event, CortinaEvent)
        self.assertTrue(event.obstruida)

        # Teste de redefinição de cota
        mock.set_position(3000)
        self.assertEqual(mock.get_position(), 3000)

        mock.cleanup()


if __name__ == "__main__":
    unittest.main()
