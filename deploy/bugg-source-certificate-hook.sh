#!/bin/sh
set -eu
[ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/mirror.bugg.icu ] || exit 0
install -m 644 "$RENEWED_LINEAGE/fullchain.pem" /etc/nginx/ssl/mirror.bugg.icu/fullchain.pem
install -m 600 "$RENEWED_LINEAGE/privkey.pem" /etc/nginx/ssl/mirror.bugg.icu/privkey.pem
/usr/sbin/nginx -t
/usr/bin/systemctl reload nginx
