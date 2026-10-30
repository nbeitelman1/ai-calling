#!/usr/bin/env bash
# tunl.online reserved-subdomain tunnel for the AI calling server.
# Mirrors the SMS relay's tunnel (sms-relay/bin/tunl-tunnel.sh):
# tunl's gateway drops non-PTY sessions after ~30s, so the ssh client
# runs under `script` to allocate a pseudo-TTY (equivalent to ssh -t).
# Forwards public https://musecallingthing.tunl.online to 127.0.0.1:8001.
exec script -qefc "ssh -tt -p 2222 -i /home/hatch/.ssh/tunl_calling -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o IdentitiesOnly=yes -o 'ProxyCommand=/home/hatch/.config/proxy-connect.sh tunl.online 2222' -R 80:127.0.0.1:8001 musecallingthing@tunl.online" /dev/null
