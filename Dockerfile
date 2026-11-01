FROM python:3.12-slim

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd -u 10001 -m appuser
COPY app/ .

USER 10001
EXPOSE 8080
# 1プロセス＋スレッドで動かす（起動時のテーブル作成を1回にするため）
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "1", "--threads", "8", "--access-logfile", "-", "app:app"]
