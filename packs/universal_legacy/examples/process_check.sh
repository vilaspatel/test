#!/usr/bin/env bash
set -euo pipefail

ps -eo pid,ppid,user,%cpu,%mem,cmd --sort=-%cpu \
| awk 'BEGIN {
  print "["
}
NR>1 {
  printf "  {\"pid\":%s,\"ppid\":%s,\"user\":\"%s\",\"cpu\":%s,\"mem\":%s,\"cmd\":\"",
         $1,$2,$3,$4,$5;
  for (i=6;i<=NF;i++) printf "%s ", $i;
  print "\"},"
}
END {
  print "]"
}'
