#!/usr/bin/env bash
# Compile the Android engine and drive it against the fake router.
#
# The engine is plain Java with no dependencies, so this needs only a JDK. The
# fake router is the Python one from tools/, which speaks whatever a definition
# says. Together they prove the Android code path executes the same documents
# the Python client does.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${TMPDIR:-$HOME/.hermes/cache/scratch}/routerengine-classes"
rm -rf "$OUT"
mkdir -p "$OUT"

echo "compiling android engine"
javac -Xlint:all -Werror -d "$OUT" \
  $(find "$ROOT/android/engine/java" "$ROOT/android/engine/test" -name '*.java')

fail=0
for api in "$ROOT"/registry/*/*/*/api.json; do
  dir="$(dirname "$api")"
  port=$(( 18000 + RANDOM % 2000 ))
  python3 "$ROOT/tools/mockrouter.py" "$api" "$port" >/dev/null 2>&1 &
  mock=$!
  # wait for the port to accept, up to 5 s
  for _ in $(seq 1 50); do
    if python3 - "$port" <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(0.1)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
    then break; fi
    sleep 0.1
  done
  if ! java -cp "$OUT" JavaSelftest "$api" "$port"; then
    echo "  ^ failed: $dir"
    fail=1
  fi
  kill "$mock" 2>/dev/null || true
  wait "$mock" 2>/dev/null || true
done

if [ "$fail" -ne 0 ]; then
  echo "android engine self test failed"
  exit 1
fi
echo "android engine self test passed"
