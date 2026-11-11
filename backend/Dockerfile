FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY ostraka/__init__.py ostraka/__init__.py
RUN pip install --no-cache-dir .

COPY . .
RUN pip install --no-cache-dir --no-deps . \
 && useradd --uid 1000 --create-home ostraka \
 && chown -R ostraka:ostraka /app
USER ostraka

EXPOSE 6176
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "ostraka.main:app", "--host", "0.0.0.0", "--port", "6176", "--proxy-headers", "--forwarded-allow-ips", "*"]
