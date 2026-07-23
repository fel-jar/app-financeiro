"""Roda sync.py + telegram_diario.py 1x por dia, num horário fixo.

Pensado pra rodar como processo/container próprio ("scheduler") separado
do app web -- fica em loop, dorme até o próximo horário, roda o ciclo,
repete. Sem dependência externa (sem cron do sistema, sem APScheduler).
"""
import time
from datetime import datetime, timedelta

import sync
import telegram_diario

HORARIO_DIARIO = "08:00"  # HH:MM, horário local do container/servidor


def proxima_execucao() -> datetime:
    agora = datetime.now()
    hora, minuto = map(int, HORARIO_DIARIO.split(":"))
    alvo = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    if alvo <= agora:
        alvo += timedelta(days=1)
    return alvo


def rodar_ciclo():
    print(f"[{datetime.now().isoformat()}] Sincronizando com a Pluggy...")
    try:
        sync.main()
    except Exception as e:
        print(f"Erro no sync: {e}")
        return  # não manda Telegram com dado desatualizado se o sync falhou

    print(f"[{datetime.now().isoformat()}] Enviando resumo diário no Telegram...")
    try:
        telegram_diario.main()
    except Exception as e:
        print(f"Erro ao enviar Telegram: {e}")


if __name__ == "__main__":
    print(f"[{datetime.now().isoformat()}] Rodando ciclo inicial (garante dados assim que o serviço sobe)...")
    rodar_ciclo()

    while True:
        alvo = proxima_execucao()
        espera = (alvo - datetime.now()).total_seconds()
        print(f"Próxima execução: {alvo.isoformat()} (em {espera / 3600:.1f}h)")
        time.sleep(max(espera, 1))
        rodar_ciclo()
