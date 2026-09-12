"""End-to-end smoke test against the local-disk storage backend.

Run from the project root:  python tests/smoke_test.py
Exits non-zero if any check fails.
"""
import io
import os
import pathlib
import shutil
import sys
import tempfile
import zipfile

tmp = tempfile.mkdtemp()
os.environ.update(
    ADMIN_PASSWORD="test-password-123",
    SECRET_KEY="unit-test-key",
    LOCAL_STORAGE_DIR=tmp,
    COOKIE_SECURE="false",
    MAX_UPLOAD_MB="1",
)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app

ok = True
def check(label, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {label} {extra}")

c = TestClient(app, follow_redirects=False)

check("health", c.get("/healthz").status_code == 200)
check("/ redirects to login when signed out", c.get("/").status_code == 303)
check("api requires auth", c.get("/api/files").status_code == 401)
check("bad password rejected", c.post("/api/login", json={"password": "nope"}).status_code == 401)

r = c.post("/api/login", json={"password": "test-password-123"})
check("login works", r.status_code == 200)
check("session cookie set", "fx_session" in c.cookies)
check("/ serves app when signed in", c.get("/").status_code == 200)

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("hello.txt", "hello from the other machine")
payload = buf.getvalue()

r = c.post("/api/upload", files={"file": ("Q3 report.zip", payload, "application/zip")})
check("upload zip", r.status_code == 200, r.text[:120])
stored = r.json().get("name")
check("filename sanitised/kept", stored == "Q3 report.zip", f"-> {stored}")

r = c.post("/api/upload", files={"file": ("deck.pptx", b"x" * 50, "application/octet-stream")})
check("upload pptx", r.status_code == 200, r.text[:120])

r = c.post("/api/upload", files={"file": ("dupe.zip", payload)})
r2 = c.post("/api/upload", files={"file": ("dupe.zip", payload)})
check("duplicate name deduped", r2.json().get("name") == "dupe-1.zip", f"-> {r2.json().get('name')}")

r = c.post("/api/upload", files={"file": ("evil.exe", b"MZ")})
check("blocked extension rejected", r.status_code == 400, r.json().get("error", "")[:60])

r = c.post("/api/upload", files={"file": ("big.zip", b"x" * (2 * 1024 * 1024))})
check("oversize rejected", r.status_code == 413, r.json().get("error", "")[:60])

r = c.post("/api/upload", files={"file": ("../../escape.zip", payload)})
check("path traversal name sanitised", r.json().get("name") == "escape.zip", f"-> {r.json().get('name')}")
check("nothing written outside storage dir",
      not os.path.exists(os.path.join(tmp, "..", "escape.zip")))

r = c.get("/api/files")
names = [f["name"] for f in r.json()["files"]]
check("listing shows uploads", "Q3 report.zip" in names and "deck.pptx" in names, names)
check("limits reported", r.json()["limits"]["max_mb"] == 1)

r = c.get("/api/download/Q3%20report.zip")
check("download returns bytes", r.status_code == 200 and r.content == payload, len(r.content))
check("attachment header", "attachment" in r.headers.get("content-disposition", ""))

check("download traversal blocked", c.get("/api/download/..%2F..%2Fsecret.txt").status_code == 404)
check("delete missing 404s", c.delete("/api/files/nope.zip").status_code == 404)

check("delete works", c.delete("/api/files/deck.pptx").status_code == 200)
check("deleted file gone", "deck.pptx" not in [f["name"] for f in c.get("/api/files").json()["files"]])

c.post("/api/logout")
check("logout clears session", c.get("/api/files").status_code == 401)

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + ("ALL PASSED" if ok else "FAILURES ABOVE"))
sys.exit(0 if ok else 1)
