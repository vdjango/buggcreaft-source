#!/bin/sh
# Aggregate shared 200 Mbit/s egress, IPv4 + IPv6, no client speed-test traffic.
# HTTP can borrow idle capacity up to 170 Mbit/s; relay/control have priority.
set -eu
device="${BUGG_EGRESS_DEVICE:-eth0}"
relay_port="${BUGG_RELAY_PORT:-3333}"
tc qdisc replace dev "$device" root handle 1: htb default 40
tc class replace dev "$device" parent 1: classid 1:1 htb rate 190mbit ceil 190mbit quantum 15140
tc class replace dev "$device" parent 1:1 classid 1:10 htb rate 10mbit ceil 190mbit prio 0 quantum 15140
tc class replace dev "$device" parent 1:1 classid 1:20 htb rate 29mbit ceil 190mbit prio 1 quantum 15140
tc class replace dev "$device" parent 1:1 classid 1:30 htb rate 150mbit ceil 170mbit prio 2 quantum 15140
tc class replace dev "$device" parent 1:1 classid 1:40 htb rate 1mbit ceil 190mbit prio 3 quantum 15140
for leaf in 10 20 30 40; do
    tc qdisc replace dev "$device" parent "1:$leaf" handle "$leaf:" fq_codel
done
for family in ip ipv6; do
    offset=0
    if [ "$family" = ipv6 ]; then offset=100; fi
    tc filter replace dev "$device" parent 1: protocol "$family" pref $((offset+10)) flower ip_proto tcp src_port 22 classid 1:10
    tc filter replace dev "$device" parent 1: protocol "$family" pref $((offset+20)) flower ip_proto tcp src_port "$relay_port" classid 1:20
    tc filter replace dev "$device" parent 1: protocol "$family" pref $((offset+21)) flower ip_proto udp src_port "$relay_port" classid 1:20
    tc filter replace dev "$device" parent 1: protocol "$family" pref $((offset+30)) flower ip_proto tcp src_port 80 classid 1:30
    tc filter replace dev "$device" parent 1: protocol "$family" pref $((offset+31)) flower ip_proto tcp src_port 443 classid 1:30
done
