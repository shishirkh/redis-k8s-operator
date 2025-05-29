FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY handler.py .
ENTRYPOINT ["kopf", "run", "handler.py", "--standalone"]
