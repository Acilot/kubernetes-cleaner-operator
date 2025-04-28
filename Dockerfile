FROM python:3.11-slim
RUN pip install kopf kubernetes
COPY /app/main.py /app/main.py
WORKDIR /app
ENTRYPOINT ["kopf", "run", "--standalone", "main.py"]
