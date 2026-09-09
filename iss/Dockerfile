FROM python:3.11-slim

ADD .bashrc /root/

COPY requirements.txt /app/requirements.txt

COPY *.py /app/

WORKDIR /app

RUN pip install --no-cache-dir -r requirements.txt

CMD python ./app.py
