FROM docker:27-cli AS docker-cli
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

COPY app.py /app/app.py
COPY templates /app/templates
COPY static /app/static
COPY config /app/config

EXPOSE 18080

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:18080", "app:app", "--timeout", "120"]
