import base64, json, urllib.request, sys, time
img_path = sys.argv[1]
with open(img_path, 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()
print(f"image bytes={len(b64)} (b64 chars)", file=sys.stderr)
payload = {
    "model": "Gemma-4-12B-OBLITERATED-Q4_K_M",
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": "Describe this image in one sentence."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
    ]}],
    "max_tokens": 100,
}
req = urllib.request.Request("http://localhost:8080/v1/chat/completions",
    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        body = json.loads(r.read())
    dt = time.time() - t0
    msg = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = body.get("usage", {})
    print(f"OK in {dt:.1f}s", file=sys.stderr)
    print(f"usage={json.dumps(usage)}", file=sys.stderr)
    print("CONTENT:", msg[:500] if msg else "(EMPTY)")
except Exception as e:
    dt = time.time() - t0
    print(f"FAIL in {dt:.1f}s: {type(e).__name__}: {e}", file=sys.stderr)
    print("FAIL")
