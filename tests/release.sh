#!/bin/sh
# Release: wait for the push build, build with the version, promote to stable. Usage: sh tests/release.sh 1.0.18
# GitHub sometimes cancels a job that waited too long for a runner; a failed run is re-run (failed jobs only) up to twice.
v="$1"; [ -n "$v" ] || { echo "usage: release.sh <version>"; exit 1; }
cd "$(dirname "$0")/.." || exit 1
grep -q "\"version\": \"$v\"" app/release_notes.json || { echo "app/release_notes.json has no entry for $v: rename the \"next\" entry to $v (and set its date) before releasing"; exit 1; }
last() { gh run list --limit 1 --json databaseId -q '.[0].databaseId'; }
finish() {   # finish <run id>: wait for it, retrying failed jobs twice; returns 0 on success
  id="$1"; tries=0
  while :; do
    gh run watch "$id" --exit-status >/dev/null 2>&1 && return 0
    tries=$((tries + 1)); [ "$tries" -gt 2 ] && return 1
    echo "run $id did not succeed; re-running its failed jobs (attempt $tries)"; gh run rerun "$id" --failed >/dev/null 2>&1; sleep 20
  done
}
sh tools/publish_content.sh || echo "warning: the public Release Notes and Roadmap could not be published"   # documentation is part of the release: the public copy is current before anything else
# public AGPL mirror (PUBLIC_MIRROR.md): OFF until the owner sets PUBLISH_PUBLIC=1 (for example in the shell profile); builds, scans and pushes one commit "ArkSignal $v"
if [ "${PUBLISH_PUBLIC:-0}" = 1 ]; then
  sh tools/publish_public.sh --version "$v" --push || { echo "the public mirror was not updated (see the scan output above): not building"; exit 1; }
  # the images are built and promoted by the PUBLIC repository's workflows (free Actions minutes); every gh call below now targets it
  GH_REPO=$(sed -n 's/^public_repo = //p' tools/public_mirror.conf); export GH_REPO
fi
sleep 20
finish "$(last)" || echo "warning: the push build did not succeed"
# start a workflow and return the id of the NEW run; GitHub sometimes answers the dispatch with a 500, so try again, and never mistake an older run for the new one
dispatch() {   # dispatch <workflow name> <version>
  before=$(last); n=0
  while :; do
    if gh workflow run "$1" -f version="$2" >/dev/null 2>&1; then
      for _ in 1 2 3 4 5 6 7 8; do sleep 10; id=$(last); [ "$id" != "$before" ] && { echo "$id"; return 0; }; done
    fi
    n=$((n + 1)); [ "$n" -ge 5 ] && return 1
    echo "dispatch of $1 did not start (attempt $n); trying again" >&2; sleep 20
  done
}
id=$(dispatch "Build and publish" "$v") || { echo "BUILD COULD NOT BE STARTED, not promoting"; exit 1; }
finish "$id" || { echo "BUILD FAILED, not promoting"; exit 1; }
echo "build $id ok"
id=$(dispatch "Promote to stable" "$v") || { echo "PROMOTE COULD NOT BE STARTED"; exit 1; }
finish "$id" || { echo "PROMOTE FAILED"; exit 1; }
sleep 5
# the real proof: the stable tag on Docker Hub now carries the same image as the version tag
if curl -s "https://hub.docker.com/v2/repositories/mallison01/adsb-portal/tags?page_size=20" | python3 -c "
import sys, json
t = {x['name']: x['images'][0]['digest'] for x in json.load(sys.stdin)['results'] if x.get('images')}
sys.exit(0 if t.get('stable') and t.get('stable') == t.get('v$v') else 1)" 2>/dev/null || curl -s "https://hub.docker.com/v2/repositories/mallison01/adsb-portal/tags?page_size=20" | python -c "
import sys, json
t = {x['name']: x['images'][0]['digest'] for x in json.load(sys.stdin)['results'] if x.get('images')}
sys.exit(0 if t.get('stable') and t.get('stable') == t.get('v$v') else 1)"; then
  bad=0
  for img in portal grafana prometheus stack mlat backup planefence photon predict aprs acars mesh mesh-mqtt; do   # every adsb-* image must carry the new version on stable
    curl -s "https://hub.docker.com/v2/repositories/mallison01/adsb-$img/tags?page_size=30" | python3 -c "
import sys, json
t = {x['name']: x['images'][0]['digest'] for x in json.load(sys.stdin)['results'] if x.get('images')}
sys.exit(0 if t.get('stable') and t.get('stable') == t.get('v$v') else 1)" 2>/dev/null || { echo "adsb-$img: stable is NOT v$v"; bad=1; }
  done
  # GitHub Container Registry is the official home of the images: the same check there (anonymous token, the digest of stable against the digest of the version tag)
  for img in portal grafana prometheus stack mlat backup planefence photon predict aprs acars mesh mesh-mqtt; do
    tok=$(curl -s "https://ghcr.io/token?scope=repository:arksignal/arksignal-$img:pull" | python3 -c "import sys, json; print(json.load(sys.stdin).get('token', ''))" 2>/dev/null)
    dg() { curl -sI -H "Authorization: Bearer $tok" -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json" "https://ghcr.io/v2/arksignal/arksignal-$img/manifests/$1" | tr -d '\r' | sed -n 's/^[Dd]ocker-[Cc]ontent-[Dd]igest: //p'; }
    a=$(dg stable); b=$(dg "v$v")
    [ -n "$a" ] && [ "$a" = "$b" ] || { echo "ghcr.io/arksignal/arksignal-$img: stable is NOT v$v"; bad=1; }
  done
  [ "$bad" = 0 ] && echo "PROMOTED $v (all ten images checked on Docker Hub and on GHCR)" || { echo "PROMOTE RAN BUT SOME IMAGES ARE NOT AT v$v"; exit 1; }
else echo "PROMOTE RAN BUT stable IS NOT v$v ON DOCKER HUB"; exit 1; fi
