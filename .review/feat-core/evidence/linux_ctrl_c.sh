#!/bin/sh
# Live drive of real Ctrl-C (SIGINT) on Linux, in a throwaway python:3.15-slim container.
# Run from the repo root:
#   docker run --rm -v "$PWD:/src:ro" python:3.15-slim sh /src/.review/feat-core/evidence/linux_ctrl_c.sh
set -u
cp -r /src /tmp/app && rm -rf /tmp/app/.venv /tmp/app/notes
pip install -q --root-user-action=ignore /tmp/app >/dev/null 2>&1 || { echo "install failed"; exit 1; }
python --version

cat > /tmp/server.py <<'EOF'
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
BIG = "".join(f'<a href="/p/{n}?q={n}">{n}</a><p>x</p>' for n in range(40000)).encode()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/slow"):
            time.sleep(60)
        body = BIG if self.path.startswith("/big") else b'<a href="/next">n</a>'
        self.send_response(200); self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)
ThreadingHTTPServer(("127.0.0.1", 8000), H).serve_forever()
EOF
python /tmp/server.py & SERVER=$!
sleep 1
U=http://127.0.0.1:8000

echo "=== scenario 1: one Ctrl-C while one fetch is stuck ==="
linkx --allow-private --concurrency 1 $U/fast1 $U/slow $U/fast2 --out /tmp/one.jsonl 2>/tmp/one.err & PID=$!
sleep 2; kill -INT $PID; wait $PID; echo "exit=$?"
cat /tmp/one.jsonl | python -c "import sys,json; [print(r['index'], r['url'][-6:], r['outcome']) for r in map(json.loads, sys.stdin)]"
grep -E "interrupt|summary|outcomes" /tmp/one.err

echo "=== scenario 2: two Ctrl-C, the second during the drain of large pages ==="
linkx --allow-private --concurrency 8 --max-bytes 10000000 $U/big1 $U/big2 $U/big3 $U/big4 $U/big5 $U/big6 $U/slow --out /tmp/two.jsonl 2>/tmp/two.err & PID=$!
sleep 1.5; kill -INT $PID; sleep 0.3; kill -INT $PID; wait $PID; echo "exit=$?"
cat /tmp/two.jsonl | python -c "import sys,json; rs=list(map(json.loads, sys.stdin)); print(len(rs), 'records,', sorted({r['index'] for r in rs}) == list(range(7)) and 'one per job' or 'MISMATCH'); [print(r['index'], r['url'][-5:], r['outcome']) for r in sorted(rs, key=lambda r: r['index'])]"
grep -E "interrupt|outcomes" /tmp/two.err

kill $SERVER
