#!/bin/sh
# Password-protects the whole site when MTG_WEB_AUTH_PASSWORD is set; the
# nginx image runs /docker-entrypoint.d/*.sh before starting nginx. Unset
# (the dev stack), /etc/nginx/auth.conf stays empty and the site is open.
set -eu

conf=/etc/nginx/auth.conf
users=/etc/nginx/htpasswd

if [ -n "${MTG_WEB_AUTH_PASSWORD:-}" ]; then
    # {PLAIN}: nginx's RFC 2307 scheme, so no hashing tool is needed. The
    # file never leaves the container, and the env var holds the same secret.
    printf '%s:{PLAIN}%s\n' "${MTG_WEB_AUTH_USER:-admin}" "$MTG_WEB_AUTH_PASSWORD" > "$users"
    chown root:nginx "$users"
    chmod 640 "$users"
    printf 'auth_basic "mtg-rules";\nauth_basic_user_file %s;\n' "$users" > "$conf"
    echo "basic auth: on (user ${MTG_WEB_AUTH_USER:-admin})"
else
    : > "$conf"
    echo "basic auth: off"
fi
