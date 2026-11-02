import json, os, secrets, time
from typing import Literal
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from . import db, ml, twin

load_dotenv()
ADMINS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
TECHS = {e.strip().lower() for e in os.getenv("TECH_EMAILS", "").split(",") if e.strip()}
BASE = os.getenv("BASE_URL", "http://localhost:8000")
DEV = os.getenv("AUTH_DEV") == "1"

app = FastAPI(title="AquaSense API")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET") or secrets.token_hex(32),
                   https_only=BASE.startswith("https"), same_site="lax", max_age=60 * 60 * 24 * 7)
oauth = OAuth()
oauth.register("google", server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
               client_id=os.getenv("GOOGLE_CLIENT_ID"), client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
               client_kwargs={"scope": "openid email profile"})
db.init()

def role_of(email: str) -> str:
    e = email.lower()
    return "admin" if e in ADMINS else "technician" if e in TECHS else "customer"

# ---------- auth ----------
@app.get("/auth/login")
async def login(request: Request):
    return await oauth.google.authorize_redirect(request, f"{BASE}/auth/callback")

@app.get("/auth/callback")
async def callback(request: Request):
    tok = await oauth.google.authorize_access_token(request)
    info = tok.get("userinfo") or {}
    if not info.get("email_verified"):
        raise HTTPException(403, "Google account email is not verified")
    request.session["user"] = {"email": info["email"].lower(), "name": info.get("name", ""), "picture": info.get("picture", "")}
    return RedirectResponse("/")

@app.get("/auth/dev")
async def dev_login(request: Request, email: str = "demo@example.com"):
    if not DEV: raise HTTPException(404)
    request.session["user"] = {"email": email.lower(), "name": email.split("@")[0], "picture": ""}
    return RedirectResponse("/")

@app.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

def user(request: Request):
    u = request.session.get("user")
    if not u: raise HTTPException(401, "Sign in required")
    return {**u, "role": role_of(u["email"])}

def staff(u=Depends(user)):
    if u["role"] == "customer": raise HTTPException(403, "Staff only")
    return u

def admin(u=Depends(user)):
    if u["role"] != "admin": raise HTTPException(403, "Admin only")
    return u

def own_device(device_id: str, u):
    with db.conn() as c:
        d = c.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    if not d or (d["owner"] != u["email"] and u["role"] == "customer"): raise HTTPException(404, "Device not found")
    return d

@app.get("/api/me")
async def me(request: Request):
    u = request.session.get("user")
    return {**u, "role": role_of(u["email"]), "dev_login": DEV} if u else {"role": None, "dev_login": DEV}

# ---------- devices and telemetry ----------
class Telemetry(BaseModel):
    tds_in: float = Field(ge=0, le=5000); tds_out: float = Field(ge=0, le=5000)
    flow: float = Field(ge=0, le=10); pressure: float = Field(ge=0, le=15)
    current: float = Field(ge=0, le=10); build: float = Field(ge=0, le=600)
    uv: float = Field(ge=0, le=50000); temp: float = Field(ge=-10, le=80)

class NewDevice(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    source: Literal["municipal", "borewell", "tanker"] = "municipal"

@app.post("/api/devices")
async def add_device(body: NewDevice, u=Depends(user)):
    did, key = db.new_id("P-"), secrets.token_urlsafe(24)
    with db.conn() as c:
        c.execute("INSERT INTO devices VALUES(?,?,?,?,?)", (did, u["email"], body.name, body.source, key))
    return {"id": did, "device_key": key, "note": "Store the key on the purifier. It is shown once."}

@app.get("/api/devices")
async def my_devices(u=Depends(user)):
    if u["role"] != "customer":
        q, a = "SELECT id,name,source,owner FROM devices", ()
    else:
        q, a = "SELECT id,name,source,owner FROM devices WHERE owner=?", (u["email"],)
    with db.conn() as c:
        return [dict(r) for r in c.execute(q, a)]

@app.post("/api/telemetry")
async def ingest(body: Telemetry, x_device_id: str = Header(), x_device_key: str = Header()):
    with db.conn() as c:
        d = c.execute("SELECT key FROM devices WHERE id=?", (x_device_id,)).fetchone()
    if not d or not secrets.compare_digest(d["key"], x_device_key): raise HTTPException(401, "Bad device credentials")
    db.add_telemetry(x_device_id, body.model_dump())
    return {"ok": True}

@app.get("/api/devices/{device_id}/health")
async def health(device_id: str, u=Depends(user)):
    d = own_device(device_id, u)
    data, ts = db.latest(device_id)
    if not data: return {"device": device_id, "status": "no data yet"}
    return {"device": device_id, "name": d["name"], "source": d["source"], "updated": ts, "telemetry": data,
            **twin.evaluate(data, d["source"]), "ml": ml.predict(data)}

class TwinIn(Telemetry):
    source: Literal["municipal", "borewell", "tanker"] = "municipal"

@app.post("/api/twin/evaluate")
async def twin_lab(body: TwinIn, u=Depends(user)):
    t = body.model_dump(); src = t.pop("source")
    return {**twin.evaluate(t, src), "ml": ml.predict(t)}

@app.get("/api/devices/{device_id}/history")
async def history(device_id: str, limit: int = 200, u=Depends(user)):
    own_device(device_id, u)
    with db.conn() as c:
        rows = c.execute("SELECT ts,data FROM telemetry WHERE device=? ORDER BY ts DESC LIMIT ?", (device_id, min(limit, 1000))).fetchall()
    return [{"ts": r["ts"], **json.loads(r["data"])} for r in rows][::-1]

# ---------- service tickets ----------
class NewTicket(BaseModel):
    device: str

@app.post("/api/tickets")
async def book(body: NewTicket, u=Depends(user)):
    d = own_device(body.device, u)
    data, _ = db.latest(body.device)
    r = twin.evaluate(data, d["source"]) if data else None
    tid = db.new_id("T-")
    with db.conn() as c:
        c.execute("INSERT INTO tickets VALUES(?,?,?,?,?,?,?,?)", (tid, d["owner"], body.device,
                  r["diagnosis"]["title"] if r else "Service requested", r["diagnosis"]["action"] if r else "Inspect",
                  r["min_health"] if r else None, "open", time.time()))
    return {"id": tid}

@app.get("/api/tickets")
async def tickets(u=Depends(user)):
    if u["role"] != "customer":
        q, a = "SELECT * FROM tickets ORDER BY ts DESC", ()
    else:
        q, a = "SELECT * FROM tickets WHERE owner=? ORDER BY ts DESC", (u["email"],)
    with db.conn() as c:
        return [dict(r) for r in c.execute(q, a)]

class Status(BaseModel):
    status: Literal["open", "scheduled", "resolved"]

@app.patch("/api/tickets/{tid}")
async def set_status(tid: str, body: Status, u=Depends(staff)):
    with db.conn() as c:
        n = c.execute("UPDATE tickets SET status=? WHERE id=?", (body.status, tid)).rowcount
    if not n: raise HTTPException(404)
    return {"ok": True}

# ---------- fleet (admin) ----------
@app.get("/api/fleet")
async def fleet(u=Depends(admin)):
    out, total = [], 0
    with db.conn() as c:
        devs = c.execute("SELECT * FROM devices").fetchall()
    for d in devs:
        data, _ = db.latest(d["id"])
        if not data: continue
        r = twin.evaluate(data, d["source"]); cost = twin.expected_repair_cost(r); total += cost
        out.append({"device": d["id"], "name": d["name"], "issue": r["diagnosis"]["title"], "part": r["diagnosis"]["part"],
                    "min_health": r["min_health"], "expected_cost_inr": cost})
    return {"devices": sorted(out, key=lambda x: x["min_health"]), "expected_repair_cost_inr": total}

app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "static")), name="static")

@app.get("/")
async def index(): return FileResponse(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"))
