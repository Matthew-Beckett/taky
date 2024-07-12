# First stage: builder
FROM python:3.11 AS builder

ENV TAKY_VERSION=0.10
ENV PUBLIC_IP=192.168.0.60

WORKDIR /build

RUN git clone --depth 1 https://github.com/tkuester/taky.git -b ${TAKY_VERSION}

WORKDIR /build/taky

RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install -r requirements.txt && \
    python3 setup.py install && \
    find /usr/local -name '*.pyc' -delete && \
    find /usr/local -name '__pycache__' -type d -exec rm -rf {} +

RUN takyctl setup --public-ip=${PUBLIC_IP} /etc/taky


# Second stage: runtime
FROM python:3.11-slim AS runtime

WORKDIR /

RUN mkdir /var/taky

COPY --from=builder /usr/local /usr/local
COPY --from=builder /etc/taky /etc/taky

WORKDIR /var/taky

ENTRYPOINT [ "taky", "-l", "info", "-c", "/etc/taky/taky.conf" ]

## Simple quickstart launch sequence:  
#  docker build --tag taky_server . && docker run -it --rm --name taky_server -p 8089:8089 -p 8090:8090 taky_server
#  echo "Generating and extracting initial client connection package..."
#  docker exec -it taky_server takyctl build_client CLIENT1 
#  docker cp taky_server:/var/taky/CLIENT1.zip ./
#  #and/or:
#  docker exec -it taky_server /bin/sh -c 'cd /var/taky ; python3 -m http.server 8090'
