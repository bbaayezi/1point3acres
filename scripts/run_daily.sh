#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
state_root="${ONEPOINT3ACRES_STATE_ROOT:-${HOME}/.local/state/onepoint3acres}"
key_file="${state_root}/2captcha.key"
cookie_file="${state_root}/cookies.json"

if [[ ! -s "${key_file}" ]]; then
  echo "2captcha key file is missing or empty: ${key_file}" >&2
  exit 2
fi
if [[ ! -s "${cookie_file}" ]]; then
  echo "cookie store is missing or empty: ${cookie_file}" >&2
  exit 2
fi
if [[ ! -x "${project_root}/.venv/bin/onepoint3acres" ]]; then
  echo "project virtual environment is not installed: ${project_root}/.venv" >&2
  exit 2
fi

export TWO_CAPTCHA_API_KEY
TWO_CAPTCHA_API_KEY="$(< "${key_file}")"
export ONEPOINT3ACRES_COOKIE_FILE="${cookie_file}"
export ONEPOINT3ACRES_PENDING_DIRECTORY="${state_root}/pending-questions"

exec "${project_root}/.venv/bin/onepoint3acres" run --non-interactive "$@"
