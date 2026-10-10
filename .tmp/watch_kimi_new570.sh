#!/usr/bin/env bash
set -u

PID="$1"
ROOT=/data1/agent_world/aw-data/kimi_k3_complete2710_20261006/new570_50x
LOG="$ROOT/supervisor.log"
MIN_FREE_KIB=$((80 * 1024 * 1024))

while kill -0 "$PID" 2>/dev/null; do
  now=$(date -Iseconds)
  free_kib=$(df -Pk /data1 | awk 'NR == 2 {print $4}')
  io_full=$(awk -F'[ =]' '/^full / {print $5}' /proc/pressure/io)
  mem_kib=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
  summary=$(find "$ROOT/resumed_forced_30x" -mindepth 1 -maxdepth 2 \
    -type f -name summary.json -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr | head -1 | cut -d' ' -f2-)
  if [[ -z "$summary" ]]; then
    summary="$ROOT/recovery_before_forced_30x/summary.json"
  fi
  if [[ -f "$summary" ]]; then
    metrics=$(jq -c '{status,processed,pending_verification,started_count,in_progress_count,case_count,completed_count,failed_count,verified_count,verifier_passed_count,verifier_failed_count,verification_error_count,not_started_count,environment_error_count,tool_timeout_count,mcp_startup_failure_count,upstream_stop_count,concurrency_target}' "$summary" 2>/dev/null || printf '{}')
  else
    metrics='{}'
  fi
  printf '%s pid=%s free_gib=%s mem_available_gib=%s io_full_avg10=%s metrics=%s\n' \
    "$now" "$PID" "$((free_kib / 1024 / 1024))" "$((mem_kib / 1024 / 1024))" \
    "$io_full" "$metrics" >>"$LOG"
  if (( free_kib < MIN_FREE_KIB )); then
    printf '%s emergency_stop=low_disk threshold_gib=80\n' "$now" >>"$LOG"
    kill -TERM -- "-$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null || true
    exit 2
  fi
  sleep 60
done

printf '%s pid=%s status=exited\n' "$(date -Iseconds)" "$PID" >>"$LOG"
