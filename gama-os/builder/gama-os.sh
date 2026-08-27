#!/usr/bin/env bash
# GAMA-OS BUILDER — jedna jezgra, sve role
# usage: sudo ./gama-os build <role> [--brand "IME"] [--url http://...]
# role: kiosk | terminal | pro | node
set -e

ROLE="${1:-kiosk}"
BRAND="${GAMA_BRAND:-GAMA TRADE}"
URL="${GAMA_URL:-http://100.104.130.34:9443}"
TELNET_BRAND=true

log()  { echo -e "\033[1;32m[GAMA-OS] $*\033[0m"; }
err()  { echo -e "\033[1;31m[GAMA-OS][ERR] $*\033[0m" >&2; exit 1; }

command -v lsb_release >/dev/null && DIST=$(lsb_release -si) || DIST="Alpine"
log "GAMA-OS build | role=$ROLE | dist=$DIST | brand=$BRAND"

install_alpine_basics() {
  apk add --no-cache alpine-base bash curl iptables tailscale nodejs npm \
    freerdp firefox 2>/dev/null || true
}

install_debian_basics() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq curl openvpn iptables git python3-pip 2>/dev/null || true
}

provision_tailscale() {
  command -v tailscale >/dev/null || return 0
  tailscale up --accept-routes=false --shields-up=false --operator=$USER 2>/dev/null \
    || log "tailscale up nije automatski — uradi rucno: sudo tailscale up"
}

kiosk_setup() {
  log "Kiosk setup — zakljucavam na: $URL"
  cat > /tmp/gama_kiosk.sh <<EOF
#!/bin/sh
# GAMA-OS KIOSK launcher
while true; do
  firefox --kiosk "$URL" --fullscreen --no-remote
  sleep 3
done
EOF
  mv /tmp/gama_kiosk.sh /usr/local/bin/gama-kiosk
  chmod +x /usr/local/bin/gama-kiosk
  # autostart (Alpine OpenRC ili systemd)
  if command -v rc-service >/dev/null; then
    cat > /etc/init.d/gama-kiosk <<'SVC'
#!/sbin/openrc-run
command="/usr/local/bin/gama-kiosk"
pidfile="/run/gama-kiosk.pid"
SVC
    chmod +x /etc/init.d/gama-kiosk
    rc-update add gama-kiosk default 2>/dev/null || true
  else
    cat > /etc/systemd/system/gama-kiosk.service <<'SVC'
[Unit] Description=GAMA-OS Kiosk After=network-online.target
[Service] ExecStart=/usr/local/bin/gama-kiosk Restart=always User=${SUDO_USER:-$USER}
[Install] WantedBy=multi-user.target
SVC
    systemctl daemon-reload; systemctl enable gama-kiosk 2>/dev/null || true
  fi
}

terminal_setup() {
  log "Terminal setup — web UI + stanka GUI"
  # Firefox bez kiosk, ali home = nas UI; SSH klijent
  kiosk_setup
}

pro_setup() {
  log "PRO setup — razvija nas pun sistem (poput Linux3)"
  # gama-deploy.sh na Oracle de odmahuje rollout; ovdje base:
  install_debian_basics
  install_alpine_basics
  provision_tailscale
}

node_setup() {
  log "NODE setup — servisni cvor (beszel, bus, semanti-mosta)"
  provision_tailscale
  command -v curl >/dev/null || install_alpine_basics
  # beszel agent (rollout od Oracle-a u pravom okruzenju)
  mkdir -p /opt/beszel-agent
}

case "$ROLE" in
  kiosk)     install_alpine_basics; provision_tailscale; kiosk_setup ;;
  terminal)  install_alpine_basics; provision_tailscale; terminal_setup ;;
  pro)       pro_setup ;;
  node)      node_setup ;;
  *) err "nepoznata rola: $ROLE" ;;
esac

log "GAMA-OS ($ROLE) build GOTOV. Brand: $BRAND"
log "Dodaj: gama-lang runtime i licencu na masini."