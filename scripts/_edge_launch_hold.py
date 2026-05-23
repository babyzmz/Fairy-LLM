import os, subprocess, tempfile, time, urllib.request, json, uuid
exe = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
port = 9466
profile = os.path.join(tempfile.gettempdir(), f'fairy_edge_profile_{uuid.uuid4().hex[:8]}')
os.makedirs(profile, exist_ok=True)
proc = subprocess.Popen([exe, '--headless', '--disable-gpu', '--no-sandbox', f'--remote-debugging-port={port}', f'--user-data-dir={profile}', 'about:blank'])
for _ in range(10):
    if proc.poll() is not None:
        break
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=2) as resp:
            pages = json.loads(resp.read().decode('utf-8','replace'))
            break
    except Exception:
        time.sleep(1)
else:
    pages = None
print(json.dumps({'pid': proc.pid, 'port': port, 'profile': profile, 'alive': proc.poll() is None, 'pages_found': bool(pages)}, ensure_ascii=False))
time.sleep(20)
if proc.poll() is None:
    proc.terminate()
    try: proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait(timeout=5)
