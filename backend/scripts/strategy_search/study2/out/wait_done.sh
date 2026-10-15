#!/bin/bash
# usage: wait_done.sh <glob prefix>  -- returns when every log's last progress line is k/k
pat="$1"
while true; do
  ok=1
  for f in $pat; do
    l=$(grep -o '[0-9]*/[0-9]* ' "$f" | tail -1 | tr -d ' ')
    [ -z "$l" ] && { ok=0; break; }
    [ "${l%/*}" = "${l#*/}" ] || { ok=0; break; }
  done
  [ $ok = 1 ] && break
  sleep 10
done
echo done
