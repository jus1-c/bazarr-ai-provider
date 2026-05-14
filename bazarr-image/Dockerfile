FROM lscr.io/linuxserver/bazarr:latest

COPY provider/aiproxy.py /tmp/aiproxy.py

RUN set -eux; \
    provider_dir="$(for root in /app /usr /opt; do \
        if [ -d "$root" ]; then \
            find "$root" -type d -path '*/custom_libs/subliminal_patch/providers' 2>/dev/null; \
        fi; \
    done | head -n 1)"; \
    test -n "$provider_dir"; \
    cp /tmp/aiproxy.py "$provider_dir/aiproxy.py"; \
    rm /tmp/aiproxy.py
