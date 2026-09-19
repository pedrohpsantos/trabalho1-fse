"""Script de diagnóstico físico de pinos na Raspberry Pi (equivalente a diagnostico.rs).

Testa estados lógicos dos pinos de entrada, monitora pulsos de encoder e testa
combinações de acionamento do motor para identificar a pinagem ativa da bancada.
"""

import sys
import time

try:
    import RPi.GPIO as GPIO
except (ImportError, RuntimeError) as e:
    print(f"❌ RPi.GPIO não está disponível neste ambiente: {e}")
    print("Este utilitário deve ser executado diretamente na Raspberry Pi conectada à bancada.")
    sys.exit(1)


def main() -> None:
    print("🔍 ========================================================")
    print("🔍 INICIANDO DIAGNÓSTICO FÍSICO DE PINOS NA RASPBERRY PI (PYTHON)")
    print("🔍 ========================================================\n")

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    # 1. Leitura do estado atual dos pinos de entrada candidatos
    print("📊 Estado atual dos pinos de entrada candidatos:")
    input_pins = [
        (0, "SENSOR_ANDAR (Cabine 1 - Tabela / Bancada 36)"),
        (11, "SENSOR_ANDAR (Cabine 1 - Widget)"),
        (12, "SENSOR_ANDAR (Cabine 2)"),
        (1, "SENSOR_ANDAR (Cabine 3)"),
        (26, "CORTINA (Cabine 1)"),
        (16, "CORTINA (Cabine 2)"),
        (19, "CORTINA (Cabine 3)"),
        (20, "ENC_A (Cabine 1)"),
        (21, "ENC_B (Cabine 1)"),
        (5, "ENC_A (Cabine 2)"),
        (6, "ENC_B (Cabine 2)"),
        (7, "ENC_A (Cabine 3)"),
        (8, "ENC_B (Cabine 3)"),
    ]

    for pin, desc in input_pins:
        try:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
            val = "HIGH (1)" if GPIO.input(pin) else "LOW  (0)"
            print(f"   GPIO {pin:>2} [ {desc:<45} ]: {val}")
        except Exception as e:
            print(f"   GPIO {pin:>2} [ {desc:<45} ]: [ERRO: {e}]")

    # 2. Monitoramento de pulsos de encoder em múltiplas portas
    print("\n📡 Configurando contadores nos encoders 20/21 e 5/6...")
    contadores = {"cab1": 0, "cab2": 0}

    def count_cab1_callback(_ch):
        contadores["cab1"] += 1

    def count_cab2_callback(_ch):
        contadores["cab2"] += 1

    for pin in (20, 21):
        try:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
            GPIO.add_event_detect(pin, GPIO.BOTH, callback=count_cab1_callback)
        except Exception:
            pass

    for pin in (5, 6):
        try:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
            GPIO.add_event_detect(pin, GPIO.BOTH, callback=count_cab2_callback)
        except Exception:
            pass

    # 3. Testa combinações de acionamento do motor
    configs = [
        ("Config A (Tabela Oficial / Bancada 36): PWM=13, DIR1=22, DIR2=23", 13, 22, 23),
        ("Config B (Widget Bancada Cab 1):        PWM=13, DIR1=17, DIR2=27", 13, 17, 27),
        ("Config C (Cabine 2):                    PWM=11, DIR1=17, DIR2=27", 11, 17, 27),
        ("Config D (Pinos invertidos):            PWM=11, DIR1=22, DIR2=23", 11, 22, 23),
    ]

    for nome, pwm_pin, dir1_pin, dir2_pin in configs:
        print(f"\n🚀 Testando {nome}")
        contadores["cab1"] = 0
        contadores["cab2"] = 0

        try:
            GPIO.setup(dir1_pin, GPIO.OUT)
            GPIO.setup(dir2_pin, GPIO.OUT)
            GPIO.setup(pwm_pin, GPIO.OUT)

            pwm = GPIO.PWM(pwm_pin, 1000.0)

            # SUBIR: DIR1=1, DIR2=0
            GPIO.output(dir1_pin, GPIO.HIGH)
            GPIO.output(dir2_pin, GPIO.LOW)
            pwm.start(60.0)

            print("   -> Acionado com duty 60% por 3 segundos...")
            time.sleep(3.0)

            # FREIO: DIR1=1, DIR2=1, PWM=0
            GPIO.output(dir1_pin, GPIO.HIGH)
            GPIO.output(dir2_pin, GPIO.HIGH)
            pwm.stop()

            c1 = contadores["cab1"]
            c2 = contadores["cab2"]
            print("   ✅ Executado com sucesso!")
            print(f"   📈 Pulsos detectados no Encoder Cabine 1 (20/21): {c1}")
            print(f"   📈 Pulsos detectados no Encoder Cabine 2 (5/6):   {c2}")
            if c1 > 0 or c2 > 0:
                print(f"   🎯 >>> SUCESSO! MOVIMENTO DETECTADO COM {nome}! <<<")
                break

        except Exception as e:
            print(f"   ❌ Falha ao acionar pinos ({pwm_pin}, {dir1_pin}, {dir2_pin}): {e}")

        time.sleep(0.5)

    try:
        GPIO.cleanup()
    except Exception:
        pass

    print("\n🏁 Diagnóstico finalizado.")


if __name__ == "__main__":
    main()
