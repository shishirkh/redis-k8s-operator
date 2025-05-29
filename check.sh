#!/bin/sh

SENTINEL_PREFIX="auth-sentinel"
N_SENTINELS="3"
N_REPLICAS="3"
RESET_INTERVAL="60"

while true; do
  echo "[INFO] $(date) - Sentinel reset loop..."
  echo "Checking..."
  for i in $(seq 0 $((N_SENTINELs - 1))); do
    echo $i
    SENTINEL="${SENTINEL_PREFIX}-$i.${SENTINEL_PREFIX}-svc.default.svc.cluster.local"
    echo "[INFO] Checking Sentinel ${SENTINEL}"

    #SLAVE_COUNT=$(redis-cli -h ${SENTINEL} -p 26379 SENTINEL slaves mymaster | grep -c '^name=')

    if [ "${SLAVE_COUNT}" -gt "${N_REPLICAS}" ]; then
      echo "[RESET] Resetting Sentinel ${SENTINEL}"
      #redis-cli -h ${SENTINEL} -p 26379 SENTINEL RESET mymaster
    fi
  done

  #sleep ${RESET_INTERVAL}
done

