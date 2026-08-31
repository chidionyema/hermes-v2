#!/usr/bin/env bash
# The boot contract (crew#736 CP2; Unbreakable Release Contract G2, founder 2026-08-31:
# "Compiling is not executing"). An image that cannot boot, import the libraries its own
# config selects, and serve its agent card never reaches the registry.
#
# Secretless on purpose: the pod comes up while External Secrets is still syncing, so a
# boot that needs a secret to stay alive is a crash-loop on the cluster. The run mirrors
# the pod: uid 10001 (gateway.yaml securityContext), read-only root, tmpfs where the
# volumes sit. The a2a plugin binds 127.0.0.1 when no token is set, so the card is read
# from inside the container.
#
# Usage: boot-contract.sh <image-ref>     (BOOT_DEADLINE seconds, default 90)
set -euo pipefail

IMAGE=${1:?usage: boot-contract.sh <image-ref>}
DEADLINE=${BOOT_DEADLINE:-90}
NAME="boot-contract-$$"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "boot-contract: 1/2 every contracted module imports, as uid 10001"
docker run --rm --user 10001:10001 --entrypoint python "$IMAGE" -c '
import importlib, os, pathlib, sys
assert os.getuid() == 10001, f"container runs as uid {os.getuid()}, not 10001"
mods = []
for ln in pathlib.Path("/app/estate/deploy/k8s/boot-contract.txt").read_text().splitlines():
    ln = ln.split("#", 1)[0].strip()
    if ln:
        mods.append(ln)
assert mods, "boot-contract.txt names no modules"
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as exc:
        bad.append(f"{m}: {type(exc).__name__}: {exc}")
if bad:
    print("\n".join(bad), file=sys.stderr)
    sys.exit(1)
print(f"boot-contract: {len(mods)} modules import")
'

echo "boot-contract: 2/2 secretless boot answers the agent card within ${DEADLINE}s"
docker run -d --name "$NAME" \
	--user 10001:10001 \
	--read-only \
	--tmpfs /tmp:uid=10001,gid=10001 \
	--tmpfs /data:uid=10001,gid=10001 \
	--env HOME=/tmp \
	"$IMAGE" >/dev/null

card=""
for _ in $(seq "$DEADLINE"); do
	if [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" != true ]; then
		echo "boot-contract: FAIL -- the container died before serving its card" >&2
		docker logs "$NAME" >&2 || true
		exit 1
	fi
	if card=$(docker exec "$NAME" curl -fsS --max-time 2 http://127.0.0.1:9900/.well-known/agent-card.json 2>/dev/null); then
		break
	fi
	card=""
	sleep 1
done
if [ -z "$card" ]; then
	echo "boot-contract: FAIL -- no 200 from /.well-known/agent-card.json in ${DEADLINE}s" >&2
	docker logs --tail 100 "$NAME" >&2 || true
	exit 1
fi
printf '%s\n' "$card" | head -c 400
echo
echo "boot-contract: PASS -- secretless boot served the agent card"
