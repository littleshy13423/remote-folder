FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends openssh-server sudo \
    && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_* \
    && useradd --create-home --shell /bin/bash rfuser \
    && install -d -m 0755 /run/sshd && install -d -m 0700 /srv/rf-private \
    && printf 'fixture marker\n' > /srv/rf-private/seed.txt \
    && touch /etc/rf-elevation-test-image
WORKDIR /opt/remote-folder
COPY requirements.txt .
# The API test runs without a desktop window.
RUN sed '/^pywebview[<>=]/d' requirements.txt > /tmp/api-requirements.txt \
    && python -m pip install --no-cache-dir -r /tmp/api-requirements.txt
COPY app/ app/
COPY static/ static/
COPY tests/elevation/entrypoint.sh tests/elevation/test_su.py ./
RUN chmod -R a+rX /opt/remote-folder
ENTRYPOINT ["/bin/sh", "/opt/remote-folder/entrypoint.sh"]
