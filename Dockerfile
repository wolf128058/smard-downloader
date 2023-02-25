FROM python:slim-buster

WORKDIR /usr/src/app

RUN apt-get update && \
    apt-get install -y locales && \
    sed -i -e 's/# de_DE.UTF-8 UTF-8/de_DE.UTF-8 UTF-8/' /etc/locale.gen && \
    dpkg-reconfigure --frontend=noninteractive locales

ENV LANG de_DE.UTF-8
ENV LC_ALL de_DE.UTF-8

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY download.py .

VOLUME /usr/src/app/downloads 

EXPOSE 8000

CMD [ "python", "./download.py" ]

