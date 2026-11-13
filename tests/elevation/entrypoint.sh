#!/bin/sh
set -eu
# This fixture changes accounts only in its disposable test image.
test -f /etc/rf-elevation-test-image && test -d /srv/rf-private || exit 1
umask 077
RF_TEST_LOGIN_PASSWORD=$(python -c 'import secrets; print(secrets.token_hex(24))')
RF_TEST_ROOT_PASSWORD=$(python -c 'import secrets; print(secrets.token_hex(24))')
printf 'rfuser:%s\nroot:%s\n' "$RF_TEST_LOGIN_PASSWORD" "$RF_TEST_ROOT_PASSWORD" | chpasswd
ssh-keygen -A >/dev/null
/usr/sbin/sshd -p 2222 -o ListenAddress=127.0.0.1 -o PermitRootLogin=no \
    -o AllowUsers=rfuser -o PasswordAuthentication=yes -o UsePAM=yes
export RF_TEST_LOGIN_PASSWORD RF_TEST_ROOT_PASSWORD
exec runuser -u rfuser -- python /opt/remote-folder/test_su.py
