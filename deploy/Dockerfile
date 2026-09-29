FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    HOME=/home/channelshift CHANNELSHIFT_HOME=/data \
    PATH=/opt/codex/bin:/opt/codex/codex-path:$PATH
ARG CHANNELSHIFT_VERSION=0.2.2
COPY channelshift-${CHANNELSHIFT_VERSION}-py3-none-any.whl /tmp/
RUN pip install --no-cache-dir /tmp/channelshift-${CHANNELSHIFT_VERSION}-py3-none-any.whl \
    && pip check \
    && useradd --uid 1000 --create-home channelshift \
    && mkdir -p /data \
    && chown 1000:1000 /data /home/channelshift
COPY codex/ /opt/codex/
USER 1000:1000
WORKDIR /home/channelshift
CMD ["channelshift-members", "--port", "5189", "--public-origin", "https://channelshift.net", "--trust-proxy-client-ip"]
