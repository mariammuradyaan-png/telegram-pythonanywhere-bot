#!/usr/bin/env bash
# First-time + ongoing PythonAnywhere deploy for this bot, driven entirely
# from the local terminal via PA's HTTP API.
#
# Reads its config from .env in the repo root. Required:
#   PA_USERNAME, PA_API_TOKEN, TELEGRAM_BOT_TOKEN, AI_API_KEY
#
# Idempotent: re-running heals partial state. Use the same script to do the
# initial deploy and to push fresh code afterward (though for ongoing pushes,
# the GitHub Actions workflow at .github/workflows/deploy.yml is simpler —
# this script is most useful for first-time setup and recovery).
#
# Two unavoidable manual steps (PA limits, not ours):
#   1. Sign up at https://www.pythonanywhere.com and grab an API token from
#      https://www.pythonanywhere.com/account/#api_token  (one time)
#   2. When the script creates a bash console, open the URL it prints in your
#      browser once so PA initializes the console. The script then drives the
#      console via the API for everything else.

set -euo pipefail

cd "$(dirname "$0")/.."

# On Git-for-Windows/MSYS, argv passed to *native* (non-MSYS) executables like
# curl and python3 gets heuristically rewritten: any argument that looks like
# a POSIX path (standalone, or embedded after a `key=`) has that portion
# silently prefixed with the MSYS install root — e.g. `/home/x/bot` becomes
# `C:/.../git/home/x/bot` — because the same pattern is how Docker/etc. pass
# real host paths. Every PA-side path this script hands to curl or python3 is
# a *remote* path, never a local one meant for translation, so exclude the
# two --data-urlencode keys that carry one plus the literal `/home/` prefix
# (for the bare-argv case when verifying the PATCH response). A blanket
# MSYS_NO_PATHCONV=1 would be simpler but also disables MSYS's /dev/null ->
# NUL mapping, breaking every `-o /dev/null` call elsewhere in this script.
# (No effect on real Linux/macOS — this var is simply ignored there.)
export MSYS2_ARG_CONV_EXCL="source_directory=;virtualenv_path=;/home/"

if [ ! -f .env ]; then
  echo "ERROR: .env not found in repo root. Copy .env.example to .env and fill it in first." >&2
  exit 1
fi

# Read .env safely: only KEY=VALUE lines, strip surrounding quotes, ignore comments.
load_env() {
  while IFS= read -r raw || [ -n "$raw" ]; do
    line="${raw%%$'\r'}"
    case "$line" in
      ''|\#*) continue ;;
    esac
    case "$line" in
      *=*) ;;
      *) continue ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    # Strip one matching pair of surrounding quotes (single or double).
    case "$value" in
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac
    export "$key=$value"
  done < .env
}
load_env

require() {
  local name="$1" hint="${2:-}"
  if [ -z "${!name:-}" ]; then
    echo "ERROR: $name is not set in .env." >&2
    [ -n "$hint" ] && echo "   $hint" >&2
    exit 1
  fi
}

require PA_USERNAME       "Your PythonAnywhere username (e.g. alicesmith)."
require PA_API_TOKEN      "Get one at https://www.pythonanywhere.com/account/#api_token"
require TELEGRAM_BOT_TOKEN "From @BotFather on Telegram."
require AI_API_KEY        "Your Cerebras / OpenAI-compatible API key."

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required but not installed." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required (used for JSON parsing)." >&2
  exit 1
fi

# On Git-for-Windows/MSYS, `curl` is usually the native mingw64 build, which
# can't read MSYS-style POSIX paths (e.g. /tmp/foo) when they're embedded in a
# composite argument like `-F content=@/tmp/foo;filename=.env` — MSYS only
# auto-translates a standalone path argument, not one glued into a bigger
# string. It fails with "curl: (26) Failed to open/read local data from
# file/application". Route paths through cygpath -w before handing them to
# curl -F when cygpath is available (i.e. on Windows); no-op elsewhere.
curl_path() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s' "$1"
  fi
}

REPO_URL="$(git remote get-url origin 2>/dev/null || true)"
if [ -z "$REPO_URL" ]; then
  echo "ERROR: this repo has no 'origin' remote. Push to GitHub first, then re-run." >&2
  exit 1
fi
REPO_NAME="$(basename "$REPO_URL" .git)"

# PA consoles have no SSH key for GitHub, so convert SSH remotes to HTTPS
# for the remote clone (works for public repos).
CLONE_URL="$REPO_URL"
case "$CLONE_URL" in
  git@github.com:*)       CLONE_URL="https://github.com/${CLONE_URL#git@github.com:}" ;;
  ssh://git@github.com/*) CLONE_URL="https://github.com/${CLONE_URL#ssh://git@github.com/}" ;;
esac

PA_API="https://www.pythonanywhere.com/api/v0/user/$PA_USERNAME"
AUTH_HEADER="Authorization: Token $PA_API_TOKEN"
DOMAIN="${PA_USERNAME}.pythonanywhere.com"
PROJECT_DIR="/home/${PA_USERNAME}/${REPO_NAME}"
VENV_DIR="/home/${PA_USERNAME}/.virtualenvs/telegram-bot"
WSGI_FILE="/var/www/${PA_USERNAME}_pythonanywhere_com_wsgi.py"
WEBHOOK_URL_RESOLVED="https://${DOMAIN}/api/webhook"
PYTHON_VERSION="python313"

echo "==> Deploying $REPO_NAME to https://${DOMAIN}"
echo "    project:  $PROJECT_DIR"
echo "    venv:     $VENV_DIR"
echo "    wsgi:     $WSGI_FILE"
echo

# Files API existence check (needs no console): true if the path exists on PA.
# GET .../files/path/<p> returns 200 for an existing file or directory, 404 if
# it's missing.
pa_path_exists() {
  local code
  code=$(curl -sS -o /dev/null -w "%{http_code}" -H "$AUTH_HEADER" "$PA_API/files/path${1}")
  [ "$code" = "200" ]
}

# --- 1. Verify API token works -----------------------------------------------
echo "==> Verifying PA API token..."
cpu_status=$(curl -sS -o /dev/null -w "%{http_code}" -H "$AUTH_HEADER" "$PA_API/cpu/")
if [ "$cpu_status" != "200" ]; then
  echo "ERROR: PA API returned $cpu_status. Check PA_USERNAME and PA_API_TOKEN in .env." >&2
  exit 1
fi

# --- 2. Create web app (idempotent) ------------------------------------------
# Detect existence via the LIST endpoint, not GET /webapps/<domain>/: PA returns
# 403 ("You do not have permission to perform this action.") — NOT 404 — for a
# domain with no web app (e.g. right after you delete one in the Web tab), so a
# per-domain status check can't tell "missing" apart from "forbidden". The list
# is unambiguous.
echo "==> Ensuring web app exists..."
webapps_json=$(curl -sS -H "$AUTH_HEADER" "$PA_API/webapps/")
webapp_exists=$(printf '%s' "$webapps_json" | python3 -c "
import json, sys
try:
    apps = json.load(sys.stdin)
except Exception:
    apps = []
print('yes' if isinstance(apps, list) and any(a.get('domain_name') == sys.argv[1] for a in apps) else 'no')
" "$DOMAIN")

if [ "$webapp_exists" = "yes" ]; then
  echo "    Web app already exists."
else
  echo "    Creating web app ($PYTHON_VERSION)..."
  create_resp=$(curl -sS -w $'\n%{http_code}' -H "$AUTH_HEADER" \
    --data-urlencode "domain_name=$DOMAIN" \
    --data-urlencode "python_version=$PYTHON_VERSION" \
    "$PA_API/webapps/")
  code="${create_resp##*$'\n'}"
  body="${create_resp%$'\n'*}"
  if [ "$code" != "201" ] && [ "$code" != "200" ]; then
    echo "ERROR: web app create failed (HTTP $code): $body" >&2
    exit 1
  fi
fi

# If the clone and virtualenv are already on PA (e.g. the web app was deleted in
# the Web tab but the home directory survived), the console-driven steps below
# are no-ops. Skip them entirely so a web-app-only recovery runs fully headless
# and doesn't need the one-time browser console init. NOTE: this skips
# `git pull` and `pip install`, so if you changed requirements.txt, delete the
# venv (or re-run from a fresh PA bash console) to force a reinstall.
if pa_path_exists "$PROJECT_DIR/.git" && pa_path_exists "$VENV_DIR/bin/python"; then
  echo "==> Clone + virtualenv already present on PA — skipping console setup."
else

# --- 3. Find or create a bash console ----------------------------------------
# Reuse an already-initialized bash console if there is one — saves the user
# the browser click on re-runs.
echo "==> Finding a usable bash console..."
consoles_json=$(curl -sS -H "$AUTH_HEADER" "$PA_API/consoles/")
CONSOLE_ID=$(python3 -c "
import json, sys
data = json.loads(sys.argv[1])
for c in data:
    if c.get('executable') == 'bash':
        print(c['id'])
        break
" "$consoles_json")

needs_browser_click=0
if [ -z "$CONSOLE_ID" ]; then
  echo "    No existing bash console. Creating one..."
  create_console=$(curl -sS -H "$AUTH_HEADER" \
    --data-urlencode "executable=bash" \
    --data-urlencode "arguments=" \
    "$PA_API/consoles/")
  CONSOLE_ID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['id'])" "$create_console")
  needs_browser_click=1
else
  echo "    Reusing existing bash console (id=$CONSOLE_ID)."
  # Existing consoles may still be uninitialized — probe by trying to read output.
  probe=$(curl -sS -o /dev/null -w "%{http_code}" -H "$AUTH_HEADER" \
    "$PA_API/consoles/$CONSOLE_ID/get_latest_output/")
  [ "$probe" = "200" ] || needs_browser_click=1
fi

if [ "$needs_browser_click" = "1" ]; then
  console_url="https://www.pythonanywhere.com/user/$PA_USERNAME/consoles/$CONSOLE_ID/"
  echo
  echo "    !!! ONE-TIME MANUAL STEP !!!"
  echo "    Open this URL in your browser, wait for the shell prompt to load, then come back:"
  echo "    $console_url"
  echo
  read -r -p "    Press Enter once the console has loaded in your browser..." _
fi

# --- 4. Drive the console: clone repo, create venv, install deps -------------
send_input() {
  # PA's send_input expects the command + a trailing newline to actually
  # press Enter. We append a marker echo so we can detect completion.
  local cmd="$1"
  curl -sS -o /dev/null -H "$AUTH_HEADER" \
    --data-urlencode "input=$cmd"$'\n' \
    "$PA_API/consoles/$CONSOLE_ID/send_input/"
}

# Wait until a unique marker shows up in the console output, then return
# 0 on the OK marker or 1 on the FAIL marker (printing recent output).
wait_for_marker() {
  local marker="$1" timeout="${2:-180}" label="${3:-command}" elapsed=0 output
  while [ "$elapsed" -lt "$timeout" ]; do
    sleep 3
    elapsed=$((elapsed + 3))
    output=$(curl -sS -H "$AUTH_HEADER" "$PA_API/consoles/$CONSOLE_ID/get_latest_output/" \
      | python3 -c "import json,sys; print(json.load(sys.stdin).get('output',''))")
    if printf '%s' "$output" | grep -q -- "${marker}_FAIL"; then
      echo "ERROR: [$label] failed on the PA console. Recent console output:" >&2
      printf '%s\n' "$output" | tail -15 >&2
      return 1
    fi
    if printf '%s' "$output" | grep -q -- "${marker}_OK"; then
      return 0
    fi
  done
  echo "ERROR: [$label] timed out waiting for marker '$marker'." >&2
  return 1
}

run_remote() {
  # Run a one-liner on the remote shell, then wait for a unique done-marker
  # that carries the command's success/failure. The single-quotes around the
  # OK/FAIL suffixes keep the *typed* command line (which PA echoes back in
  # the console output) from matching the markers we grep for — only the
  # executed echo produces the contiguous marker string.
  local label="$1" cmd="$2" timeout="${3:-180}"
  local marker="__PADEPLOY_$(date +%s%N)_$$__"
  echo "    [$label] running..."
  send_input "{ $cmd; } && echo ${marker}_'OK' || echo ${marker}_'FAIL'"
  wait_for_marker "$marker" "$timeout" "$label"
}

run_remote "git clone or pull" \
  "if [ -d $PROJECT_DIR/.git ]; then cd $PROJECT_DIR && git pull --ff-only; else git clone $CLONE_URL $PROJECT_DIR; fi" \
  120

run_remote "create venv (if missing)" \
  "[ -d $VENV_DIR ] || python3.13 -m venv $VENV_DIR" \
  60

run_remote "pip install requirements" \
  "$VENV_DIR/bin/pip install --upgrade pip && $VENV_DIR/bin/pip install -r $PROJECT_DIR/requirements.txt" \
  300

fi  # end "skip console setup when clone + venv already present" guard

# --- 4b. Ensure PA recognizes the virtualenv ---------------------------------
# PA's webapp-config PATCH (step 7) validates virtualenv_path by checking for
# bin/activate_this.py — a legacy artifact of the third-party `virtualenv`
# package. We create the venv with the stdlib `python -m venv` (no extra
# dependency needed), which never writes that file, so the PATCH fails with
# "Warning: No virtualenv detected at this path." Upload the standard
# activate_this.py content directly via the Files API — no console needed, so
# this also self-heals venvs created before this fix, even via the fast
# headless "already present" skip path above.
if ! pa_path_exists "$VENV_DIR/bin/activate_this.py"; then
  echo "==> Adding bin/activate_this.py (PA virtualenv marker)..."
  TMP_ACTIVATE="$(mktemp -t pa_activate.XXXXXX)"
  cat > "$TMP_ACTIVATE" <<'PYEOF'
"""Activate virtualenv for current interpreter.

Use exec(open(this_file).read(), {'__file__': this_file}).

This can be used when you must use an existing Python interpreter, not the
virtualenv bin/python.
"""
import os
import site
import sys

try:
    abs_file = os.path.abspath(__file__)
except NameError:
    raise AssertionError("You must use exec(open(this_file).read(), {'__file__': this_file}))")

bin_dir = os.path.dirname(abs_file)
base = bin_dir[: -len("bin") - 1]  # strip away the bin part from the __file__, plus the path separator

sys.path[0:0] = [bin_dir]
os.environ["VIRTUAL_ENV"] = base  # virtual env is right above bin directory

if sys.platform == "win32":
    site_packages = os.path.join(base, "Lib", "site-packages")
else:
    site_packages = os.path.join(base, "lib", "python%s" % sys.version[:3], "site-packages")

prev_length = len(sys.path)
site.addsitedir(site_packages)
sys.path[:] = sys.path[prev_length:] + sys.path[0:prev_length]

sys.real_prefix = sys.prefix
sys.prefix = base
PYEOF
  activate_status=$(curl -sS -o /dev/null -w "%{http_code}" -X POST -H "$AUTH_HEADER" \
    -F "content=@$(curl_path "$TMP_ACTIVATE");filename=activate_this.py" \
    "$PA_API/files/path${VENV_DIR}/bin/activate_this.py")
  rm -f "$TMP_ACTIVATE"
  case "$activate_status" in
    200|201) ;;
    *) echo "ERROR: activate_this.py upload failed (HTTP $activate_status)." >&2; exit 1 ;;
  esac
fi

# --- 5. Upload .env to PA ----------------------------------------------------
echo "==> Generating PA-side .env..."
TMP_ENV="$(mktemp -t pa_env.XXXXXX)"
trap 'rm -f "$TMP_ENV"' EXIT

emit() { printf '%s=%s\n' "$1" "$2" >> "$TMP_ENV"; }
emit_if_set() { if [ -n "${!1:-}" ]; then emit "$1" "${!1}"; fi; }

emit TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
emit AI_API_KEY         "$AI_API_KEY"
emit AI_BASE_URL        "${AI_BASE_URL:-https://api.cerebras.ai/v1}"
emit AI_MODEL           "${AI_MODEL:-gpt-oss-120b}"
emit SQLITE_PATH        "${SQLITE_PATH:-/home/$PA_USERNAME/bot.db}"
emit WEBHOOK_URL        "${WEBHOOK_URL:-$WEBHOOK_URL_RESOLVED}"
emit HOSTING_LABEL      "${HOSTING_LABEL:-PythonAnywhere}"
emit RATE_LIMIT         "${RATE_LIMIT:-250}"
emit_if_set WEBHOOK_SECRET
emit_if_set ALLOWED_USERS
emit_if_set HF_SPACE_ID
emit_if_set HF_TOKEN
emit_if_set DEPLOY_SECRET

echo "==> Uploading .env to $PROJECT_DIR/.env ..."
upload_status=$(curl -sS -o /dev/null -w "%{http_code}" -X POST -H "$AUTH_HEADER" \
  -F "content=@$(curl_path "$TMP_ENV");filename=.env" \
  "$PA_API/files/path${PROJECT_DIR}/.env")
case "$upload_status" in
  200|201) ;;
  *) echo "ERROR: .env upload failed (HTTP $upload_status)." >&2; exit 1 ;;
esac

# --- 6. Upload the PA-side WSGI file ----------------------------------------
TMP_WSGI="$(mktemp -t pa_wsgi.XXXXXX)"
trap 'rm -f "$TMP_ENV" "$TMP_WSGI"' EXIT
cat > "$TMP_WSGI" <<EOF
import sys

project_home = "$PROJECT_DIR"
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from pythonanywhere_wsgi import application  # noqa: F401
EOF

echo "==> Uploading WSGI file to $WSGI_FILE ..."
wsgi_status=$(curl -sS -o /dev/null -w "%{http_code}" -X POST -H "$AUTH_HEADER" \
  -F "content=@$(curl_path "$TMP_WSGI");filename=wsgi.py" \
  "$PA_API/files/path${WSGI_FILE}")
case "$wsgi_status" in
  200|201) ;;
  *) echo "ERROR: WSGI upload failed (HTTP $wsgi_status)." >&2; exit 1 ;;
esac

# --- 7. Point the web app at source dir + virtualenv -------------------------
# The 2026-06 outage happened because these two values drifted (source_directory
# was stuck at /var/www, virtualenv_path was blank). So we PATCH them *and* read
# the values back out of the response to confirm the change actually took — a
# 200 alone isn't proof the new values stuck.
echo "==> Configuring web app source + virtualenv..."
patch_resp=$(curl -sS -w $'\n%{http_code}' -X PATCH -H "$AUTH_HEADER" \
  --data-urlencode "source_directory=$PROJECT_DIR" \
  --data-urlencode "virtualenv_path=$VENV_DIR" \
  "$PA_API/webapps/$DOMAIN/")
patch_code="${patch_resp##*$'\n'}"
patch_body="${patch_resp%$'\n'*}"
if [ "$patch_code" != "200" ]; then
  echo "ERROR: web app config failed (HTTP $patch_code): $patch_body" >&2
  exit 1
fi
printf '%s' "$patch_body" | python3 -c "
import json, sys
d = json.load(sys.stdin)
want_sd, want_vp = sys.argv[1], sys.argv[2]
sd, vp = d.get('source_directory'), d.get('virtualenv_path')
print(f'    source_directory = {sd}')
print(f'    virtualenv_path  = {vp}')
sys.exit(0 if (sd == want_sd and vp == want_vp) else 1)
" "$PROJECT_DIR" "$VENV_DIR" || {
  echo "ERROR: web app config did not take effect (values above don't match what we set)." >&2
  exit 1
}

# --- 8. Reload ---------------------------------------------------------------
echo "==> Reloading web app..."
reload_status=$(curl -sS -o /dev/null -w "%{http_code}" -X POST -H "$AUTH_HEADER" \
  "$PA_API/webapps/$DOMAIN/reload/")
case "$reload_status" in
  200) ;;
  *) echo "WARNING: reload returned HTTP $reload_status. Try clicking Reload in the PA Web tab if the bot is silent." >&2 ;;
esac

# --- 9. Smoke test, with automatic error-log diagnosis on failure -----------
# A broken WSGI, wrong source_directory/virtualenv, or a missing dependency all
# surface here as a non-200. Reloads are async and a cold worker can take
# ~5-10s, so poll instead of a single shot. If it never comes up, pull the PA
# error log automatically — that's the log that revealed the 2026-06
# circular-import outage, so surfacing it here turns a silent failure into an
# actionable one.
echo "==> Smoke-testing /api/health (polling up to ~45s)..."
health="000"
for _ in 1 2 3 4 5 6 7 8 9; do
  sleep 5
  health=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 15 "https://$DOMAIN/api/health" || echo "000")
  if [ "$health" = "200" ]; then break; fi
done

if [ "$health" = "200" ]; then
  echo "    OK ($health) — bot is live."
else
  echo "ERROR: /api/health returned $health after reload." >&2
  echo "==> Fetching PA error log to diagnose..." >&2
  err_log=$(curl -sS -H "$AUTH_HEADER" "$PA_API/files/path/var/log/${DOMAIN}.error.log" 2>/dev/null || true)
  if [ -n "$err_log" ]; then
    echo "----- last 25 lines of /var/log/${DOMAIN}.error.log -----" >&2
    printf '%s\n' "$err_log" | tail -25 >&2
    echo "----------------------------------------------------------" >&2
  else
    echo "    (could not fetch the error log via API — check the PA Web tab.)" >&2
  fi
  echo "    Re-running this script heals config/WSGI drift; if a dependency is" >&2
  echo "    missing, the pip step above installs it." >&2
  exit 1
fi

echo
echo "==> Done. Bot is live at https://$DOMAIN"
echo
echo "    Send your bot a message on Telegram to try it."
echo "    Updates from here on: just push to main (the GitHub Action auto-deploys)."
echo "    Or re-run this script — it's idempotent."
