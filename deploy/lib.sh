# Shared by the service run scripts. All persistent state lives under DOCQA_DATA_DIR,
# which on Runpod is on the network volume and so survives the pod.
set -euo pipefail

DATA="${DOCQA_DATA_DIR:-/workspace/docqa}"
export HF_HOME="${HF_HOME:-$DATA/huggingface}"

# s6 starts each run script inside its own service directory, which gives the service name.
SERVICE="$(basename "$PWD")"
log() { echo "[$SERVICE] $*"; }

# wait_for <name> <command...>: block until the command succeeds.
wait_for() {
  local name="$1"; shift
  until "$@" >/dev/null 2>&1; do sleep 1; done
  log "$name is up"
}

http_ok() { curl -fsS --max-time 3 "$1"; }

models_enabled() { [ "${DOCQA_MODELS:-on}" = "on" ]; }

# A service that is switched off must still stay "running" for the supervisor.
idle_forever() { log "$1"; exec sleep infinity; }

# The JWT signing key is generated once and kept on the volume unless one is supplied.
load_jwt_secret() {
  if [ -z "${DOCQA_JWT_SECRET:-}" ]; then
    local file="$DATA/secrets/jwt_secret"
    if [ ! -s "$file" ]; then
      mkdir -p "$(dirname "$file")"
      (umask 077; head -c 48 /dev/urandom | base64 | tr -d '\n' > "$file")
      chown docqa:docqa "$file"
    fi
    DOCQA_JWT_SECRET="$(cat "$file")"
    export DOCQA_JWT_SECRET
  fi
}
