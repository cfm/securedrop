#!/bin/sh
# Add journalist-api-rs loopback rules to the running iptables policy.
# Insert before the www-data LOGNDROP catch-all so they are evaluated first.
# Run as root.

LOGNDROP_LINE=$(iptables -L OUTPUT --line-numbers -n | awk '/LOGNDROP.*securedrop user/{print $1; exit}')

iptables -I INPUT  -i lo -p tcp --dport 8082 -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT -m comment --comment "Allow Apache to connect to journalist-api-rs"
iptables -I OUTPUT "$LOGNDROP_LINE" -o lo -p tcp --sport 8082 -m owner --uid-owner www-data -m state --state ESTABLISHED,RELATED -j ACCEPT -m comment --comment "Allow journalist-api-rs to respond to Apache"
