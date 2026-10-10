import json
import subprocess
import sys
import time

code = r"""
import hashlib,json,math,os,sys,time
from pathlib import Path
from jobhunt_core.embeddings import SentenceTransformerBackend
started=time.monotonic()
backend=SentenceTransformerBackend('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2','e8f8c211226b894fcb81acc59f3b34ba3efd5f42')
vectors=backend.encode_batch(['Software engineer Python React','Primary school teacher English'])
memory={}
for line in Path('/proc/self/smaps_rollup').read_text().splitlines():
 key,_,value=line.partition(':')
 if key in ('Rss','Pss','Private_Dirty','SwapPss'):memory[key+'_kib']=int(value.split()[0])
print('PROBE '+json.dumps({'pid':os.getpid(),'seconds':time.monotonic()-started,'dimensions':[len(v) for v in vectors],'finite':all(math.isfinite(x) for v in vectors for x in v),'vector_hash':hashlib.sha256(json.dumps([[round(x,6) for x in v] for v in vectors]).encode()).hexdigest(),'memory':memory}),flush=True)
sys.stdin.readline()
"""
result = {}
for n in (1, 2):
    children = []
    rows = []
    started = time.monotonic()
    try:
        for _ in range(n):
            p = subprocess.Popen(
                [sys.executable, "-c", code],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            children.append(p)
            for line in p.stdout:
                if line.startswith("PROBE "):
                    rows.append(json.loads(line[6:]))
                    break
            else:
                raise RuntimeError("encoder process failed")
        from pathlib import Path

        for row in rows:
            for line in (
                Path("/proc/" + str(row["pid"]) + "/smaps_rollup")
                .read_text()
                .splitlines()
            ):
                key, _, value = line.partition(":")
                if key in ("Rss", "Pss", "Private_Dirty", "SwapPss"):
                    row["memory"][key + "_kib"] = int(value.split()[0])
        result[str(n)] = {
            "workers": rows,
            "wall_seconds": round(time.monotonic() - started, 2),
        }
        print(json.dumps({"workers_count": n, **result[str(n)]}), flush=True)
    finally:
        for p in children:
            if p.poll() is None:
                p.communicate("\n", timeout=30)
print(json.dumps({"controlled_model_comparison": result}), flush=True)
