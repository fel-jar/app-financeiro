"""Roda os ciclos periódicos do app financeiro num loop só, sem
dependência externa (sem cron do sistema, sem APScheduler):

- email_pendente.checar_email_pendente(): a cada INTERVALO_EMAIL_MINUTOS
  -- é o que dá o "tempo real" (compra aparece pendente minutos depois de
  acontecer, bem antes da Pluggy confirmar). Ver directives/agente_telegram.md,
  seção 2026-07-29.
- sync.py + telegram_diario.py: 1x/dia, HORARIO_DIARIO.
- telegram_semanal.py: 1x/semana, domingo, logo depois do ciclo diário --
  fechamento "de verdade" (só transações confirmadas).

Pensado pra rodar como processo/container próprio ("scheduler") separado
do app web.
"""
import time
from datetime import datetime, timedelta

import email_pendente
import sync
import telegram_diario
import telegram_semanal

HORARIO_DIARIO = "20:00"  # HH:MM, horário local do container/servidor -- fim do dia (pedido do usuário em 2026-07-25, era 08:00)
INTERVALO_EMAIL_MINUTOS = 15  # pedido do usuário em 2026-07-29: quase tempo real, não 1x/dia
TICK_SEGUNDOS = 30  # granularidade do loop principal


def rodar_ciclo_diario():
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
        print(f"Erro ao enviar Telegram (resumo diário): {e}")

    if datetime.now().weekday() == 6:  # domingo
        print(f"[{datetime.now().isoformat()}] Enviando fechamento semanal no Telegram...")
        try:
            telegram_semanal.main()
        except Exception as e:
            print(f"Erro ao enviar Telegram (fechamento semanal): {e}")


def rodar_ciclo_email():
    try:
        n = email_pendente.checar_email_pendente()
        if n:
            print(f"[{datetime.now().isoformat()}] {n} compra(s) pendente(s) nova(s) via e-mail.")
    except Exception as e:
        print(f"Erro checando e-mail: {e}")


def main():
    print(f"[{datetime.now().isoformat()}] Rodando ciclo inicial (garante dados assim que o serviço sobe)...")
    rodar_ciclo_diario()

    proxima_email = datetime.now() + timedelta(minutes=INTERVALO_EMAIL_MINUTOS)
    ultimo_diario_em = datetime.now().date()

    while True:
        time.sleep(TICK_SEGUNDOS)
        agora = datetime.now()

        if agora >= proxima_email:
            rodar_ciclo_email()
            proxima_email = agora + timedelta(minutes=INTERVALO_EMAIL_MINUTOS)

        if agora.strftime("%H:%M") == HORARIO_DIARIO and agora.date() != ultimo_diario_em:
            rodar_ciclo_diario()
            ultimo_diario_em = agora.date()


if __name__ == "__main__":
    main()
