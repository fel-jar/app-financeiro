FROM python:3.12-slim

ENV TZ=America/Sao_Paulo
# ffmpeg: agente_llm.py usa pra converter áudio do Telegram (ogg/opus) pra
# mp3 antes de mandar pra transcrição via OpenRouter -- sem o binário aqui,
# a conversão falha silenciosamente (cai no fallback de bytes crus) e a
# transcrição de voz não funciona de verdade.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ffmpeg \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY execution/ ./execution/

WORKDIR /app/execution
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "app:app"]
