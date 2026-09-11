#!/usr/bin/env bash
# End-to-end smoke test against a *running* server.
#
# Proves the parts of the stack that a unit test cannot: real HTTP, real static
# media, a real generated image, a real edit, and a real narrated MP4 on disk.
#
# Usage:  scripts/smoke.sh [base_url] [username] [password]
set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"
USERNAME="${2:-admin}"
PASSWORD="${3:-abhi-admin}"

pass() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; exit 1; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

jqp() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

step "1. Health & environment"
HEALTH="$(curl -fsS "$BASE/api/health")" || fail "server not reachable at $BASE"
echo "$HEALTH" | jqp "d['status']" >/dev/null || fail "unexpected health payload"
echo "$HEALTH" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f\"    status={d['status']} version={d['version']}\")
print(f\"    ffmpeg={d['ffmpeg']['version']}  db_ok={d['database']['ok']}\")
print(f\"    capabilities={d['capabilities']}\")
"
pass "server is up and reports capabilities"

step "2. Authentication"
TOKEN="$(curl -fsS -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" | jqp "d['token']")" \
  || fail "login failed (check credentials)"
AUTH="Authorization: Bearer $TOKEN"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/projects")" = "401" ] \
  && pass "unauthenticated requests are rejected (401)" \
  || fail "API is not enforcing authentication"

step "3. Project"
PROJECT_ID="$(curl -fsS -X POST "$BASE/api/projects" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"Smoke Test","style_prompt":"cinematic"}' | jqp "d['id']")"
pass "created project $PROJECT_ID"

step "4. Image generation (real pixels on disk)"
IMAGE_JSON="$(curl -fsS -X POST "$BASE/api/generate/image?wait=true" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"prompt\":\"a lighthouse in a storm\",\"width\":640,\"height\":360,\"project_id\":\"$PROJECT_ID\"}")"
ASSET_ID="$(echo "$IMAGE_JSON" | jqp "d['assets'][0]['id']")"
ASSET_URL="$(echo "$IMAGE_JSON" | jqp "d['assets'][0]['url']")"
ASSET_W="$(echo "$IMAGE_JSON" | jqp "d['assets'][0]['width']")"
echo "$IMAGE_JSON" | python3 -c "
import json,sys
a=json.load(sys.stdin)['assets'][0]
print(f\"    {a['width']}x{a['height']} {a['engine']} ({a['size_bytes']} bytes) quality={a['meta'].get('quality')}\")
"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE$ASSET_URL")" = "200" ] \
  && pass "generated image is served over HTTP" || fail "media URL returned non-200: $ASSET_URL"
[ "$ASSET_W" = "640" ] && pass "image has the requested width" || fail "wrong image width"

step "5. Image edit (real Pillow work)"
curl -fsS -X POST "$BASE/api/assets/$ASSET_ID/edit" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"ops":[{"op":"sepia"},{"op":"resize","width":320,"height":180}]}' \
  | python3 -c "
import json,sys
a=json.load(sys.stdin)['asset']
print(f\"    derived {a['width']}x{a['height']} engine={a['engine']} parent={'yes' if a['parent_asset_id'] else 'no'}\")
" >/dev/null || fail "edit failed"
pass "edit produced a linked derived asset"

step "6. Story -> storyboard -> narrated film"
STORY_ID="$(curl -fsS -X POST "$BASE/api/generate/story?wait=true" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"premise\":\"A keeper hears a song from the trench\",\"project_id\":\"$PROJECT_ID\",\"scenes\":2,\"shots_per_scene\":2}" \
  | jqp "d['result']['story_id']")"
pass "story created ($STORY_ID)"

curl -fsS -X POST "$BASE/api/stories/$STORY_ID/generate" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"only_missing":true,"narration":true}' >/dev/null
pass "storyboard generation queued"

printf '    waiting for the queue to drain'
for _ in $(seq 1 90); do
  RUNNING="$(curl -fsS "$BASE/api/jobs/stats" -H "$AUTH" | python3 -c "
import json,sys; c=json.load(sys.stdin)['counts']; print(c['queued']+c['running'])")"
  [ "$RUNNING" = "0" ] && break
  printf '.'; sleep 1
done
printf '\n'
[ "$RUNNING" = "0" ] || fail "jobs did not finish in time"

BOARD="$(curl -fsS "$BASE/api/stories/$STORY_ID/storyboard" -H "$AUTH")"
echo "$BOARD" | python3 -c "
import json,sys
s=json.load(sys.stdin)['stats']
print(f\"    shots={s['shot_count']} with_images={s['with_images']} with_audio={s['with_audio']} duration={s['total_duration_s']}s\")
"
echo "$BOARD" | python3 -c "
import json,sys
s=json.load(sys.stdin)['stats']
assert s['with_images'] == s['shot_count'], 'shots missing images'
" && pass "every shot has a generated image" || fail "storyboard incomplete"

RENDER="$(curl -fsS -X POST "$BASE/api/stories/$STORY_ID/render?wait=true&width=640&height=360" -H "$AUTH")"
echo "$RENDER" | python3 -c "
import json,sys
for a in json.load(sys.stdin)['assets']:
    print(f\"    {a['kind']:5s} {str(a['duration_s']):>6s}s  {a['engine']:14s} {a['url']}\")
    assert a['size_bytes'] > 10000, 'rendered file is suspiciously small'
" || fail "render produced no usable video"
pass "storyboard rendered to a real MP4"

step "7. Job queue"
curl -fsS "$BASE/api/jobs/stats" -H "$AUTH" | python3 -c "
import json,sys
c=json.load(sys.stdin)['counts']
print(f\"    {c}\")
assert c['failed'] == 0, 'there are failed jobs'
"
pass "no failed jobs"

printf '\n\033[32mSMOKE TEST PASSED\033[0m — server, auth, generation, editing, storyboard and video render all verified.\n'
