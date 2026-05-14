FROM lscr.io/linuxserver/bazarr:latest

COPY provider/aiproxy.py /tmp/aiproxy.py
COPY bazarr-image/install_aiproxy.py /tmp/install_aiproxy.py

RUN set -eux; \
    python3 /tmp/install_aiproxy.py; \
    rm /tmp/install_aiproxy.py
