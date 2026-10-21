#!/bin/sh
# Redeploy the demo API from GitHub main in one command (see docs/DEPLOY.md):
# fresh clone of the branch, commit stamped into the image, upload to the Zeabur API service, wait until the
# NEW deployment is RUNNING, then prove the live container runs that commit (runtime log) and /health is ok.
#
#   HM_ZEABUR_API_SERVICE_ID=<id> HM_ZEABUR_ENV_ID=<id> sh deploy/redeploy.sh
#
# Optional: HM_BRANCH (default main), HM_REPO_URL, HM_DEMO_URL, HM_ZEABUR_ACCOUNT (abort unless the zeabur
# CLI is logged in with this email; the CLI config can be switched by other sessions on the same machine).
set -eu
: "${HM_ZEABUR_API_SERVICE_ID:?set HM_ZEABUR_API_SERVICE_ID}"
: "${HM_ZEABUR_ENV_ID:?set HM_ZEABUR_ENV_ID}"
repo="${HM_REPO_URL:-https://github.com/retailbox-automation/what-leaves-with-them.git}"
branch="${HM_BRANCH:-main}"
url="${HM_DEMO_URL:-https://what-leaves.retailbox-automation.com}"
sid="$HM_ZEABUR_API_SERVICE_ID"
eid="$HM_ZEABUR_ENV_ID"

if [ -n "${HM_ZEABUR_ACCOUNT:-}" ]; then
    zeabur auth status -i=false 2>&1 | grep -q "email: ${HM_ZEABUR_ACCOUNT}," \
        || { echo "redeploy: zeabur CLI is not logged in as ${HM_ZEABUR_ACCOUNT}" >&2; exit 1; }
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
git clone --quiet --depth 1 --branch "$branch" "$repo" "$work/src"
commit="$(git -C "$work/src" rev-parse HEAD)"
printf '%s\n' "$commit" > "$work/src/deploy/COMMIT"
# The upload skips gitignored files and deploy/COMMIT is gitignored, so the clone's .gitignore goes too.
rm -rf "$work/src/.git" "$work/src/.gitignore"
echo "redeploy: $branch at $commit"

deployments() { zeabur deployment list --service-id "$sid" --env-id "$eid" --json -i=false; }
before="$(deployments | python3 -c 'import json, sys; print(" ".join(d["ID"] for d in json.load(sys.stdin)))')"

(cd "$work/src" && zeabur deploy --service-id "$sid" --environment-id "$eid" -i=false)

# The deploy command can report success on a failed build: judge by the deployment list only.
deadline=$(( $(date +%s) + 900 ))
while :; do
    state="$(deployments | python3 -c '
import json, sys
before = set(sys.argv[1].split())
new = sorted((d for d in json.load(sys.stdin) if d["ID"] not in before), key=lambda d: d.get("createdAt") or "")
print(new[-1]["ID"] + " " + new[-1]["status"] if new else "- none")
' "$before")"
    echo "redeploy: $(date -u +%H:%M:%SZ) deployment $state"
    case "$state" in
        *" RUNNING") break ;;
        # REMOVED can show for a few seconds right after the upload, before BUILDING; only these two are final.
        *" FAILED"|*" CANCELED") echo "redeploy: the new deployment did not reach RUNNING" >&2; exit 1 ;;
    esac
    [ "$(date +%s)" -lt "$deadline" ] || { echo "redeploy: timed out after 15 min" >&2; exit 1; }
    sleep 15
done

# Ground truth: the running container logged this commit (an old container would still answer 200).
deadline=$(( $(date +%s) + 300 ))
until zeabur deployment log --service-id "$sid" --env-id "$eid" -t runtime -i=false 2>/dev/null \
        | grep -q "code from commit $commit"; do
    [ "$(date +%s)" -lt "$deadline" ] || { echo "redeploy: runtime log does not show commit $commit" >&2; exit 1; }
    sleep 10
done
echo "redeploy: runtime log shows commit $commit"

deadline=$(( $(date +%s) + 300 ))
until curl -fs -m 10 "$url/health" | grep -q '"status":"ok"'; do
    [ "$(date +%s)" -lt "$deadline" ] || { echo "redeploy: $url/health is not ok" >&2; exit 1; }
    sleep 10
done
echo "redeploy: $url/health ok"
