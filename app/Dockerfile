FROM python:3.7-bullseye

RUN pip install paho-mqtt==1.6.1 pyyaml==6.0

COPY entrypoint.sh app.py /app/

WORKDIR /app/

ENTRYPOINT ["/app/entrypoint.sh"]
