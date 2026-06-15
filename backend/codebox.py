"""
codebox.py — Backend đa-AI cho app KENIOS (com.kenios.codebox)
=============================================================
Một file duy nhất, chạy trên VPS/hosting 24/7. App iOS chỉ cần:
  - Nhập URL/IP máy chủ trong Cài đặt
  - Đăng nhập tài khoản
  - Nhập API key của (các) AI -> chọn AI để chat

TÍNH NĂNG
  * Tài khoản: đăng ký / đăng nhập / quên mật khẩu / đổi Gmail-SĐT-mật khẩu
  * Lưu lịch sử hội thoại (SQLite)
  * Nhiều nhà cung cấp AI: OpenAI, Anthropic(Claude), Google Gemini,
    Groq, OpenRouter, Mistral, DeepSeek, xAI(Grok)...  (key nhập từ app)
  * Mã hóa API key khi lưu (Fernet)
  * Đính ảnh (multimodal)
  * Chế độ "đối xứng" (ensemble): hỏi nhiều AI rồi 1 AI tổng hợp ra 1 câu tốt nhất
  * Endpoint giọng nói: chuyển audio -> văn bản
  * Báo lỗi 401/403 rõ ràng (sai key / hết quyền) thay vì app bị treo

KHÔNG có: thực thi file/code tùy ý trên server (RCE) — lý do bảo mật.
KHÔNG hardcode API key trong file — key do app gửi lên.

------------------------------------------------------------------
CÀI ĐẶT (Ubuntu/Debian):
    sudo apt update && sudo apt install -y python3-venv
    python3 -m venv venv && source venv/bin/activate
    pip install "fastapi>=0.110" "uvicorn[standard]>=0.29" "httpx>=0.27" "cryptography>=42"

CHẠY:
    export CODEBOX_SECRET="chuoi-bi-mat-dai-ngau-nhien"   # tùy chọn, để ký token
    python codebox.py
    # hoặc: uvicorn codebox:app --host 0.0.0.0 --port 8000

CHẠY 24/7 (systemd) — xem hướng dẫn cuối file.
NÊN đặt sau Nginx + HTTPS (Let's Encrypt).
------------------------------------------------------------------
"""

import os
import re
import time
import json
import hmac
import base64
import hashlib
import secrets
import sqlite3
import logging
import asyncio
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ============================ Cấu hình ============================
DB_PATH = os.getenv("CODEBOX_DB", "codebox.db")
PORT = int(os.getenv("PORT", "8000"))
SECRET = os.getenv("CODEBOX_SECRET") or secrets.token_hex(32)
TOKEN_TTL = int(os.getenv("TOKEN_TTL", str(60 * 60 * 24 * 7)))  # 7 ngày
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("codebox")

# ----- Khóa mã hóa (Fernet) để mã hóa API key lưu trong DB -----
from cryptography.fernet import Fernet

_key_file = os.getenv("CODEBOX_ENC_KEYFILE", "codebox_enc.key")
if os.getenv("CODEBOX_ENC_KEY"):
    _enc_key = os.getenv("CODEBOX_ENC_KEY").encode()
elif os.path.exists(_key_file):
    _enc_key = open(_key_file, "rb").read().strip()
else:
    _enc_key = Fernet.generate_key()
    with open(_key_file, "wb") as f:
        f.write(_enc_key)
    log.info("Đã tạo khóa mã hóa mới: %s (giữ kỹ file này!)", _key_file)
fernet = Fernet(_enc_key)


def enc(text: str) -> str:
    return fernet.encrypt(text.encode()).decode()


def dec(token: str) -> str:
    return fernet.decrypt(token.encode()).decode()


# ===================== Danh sách AI hỗ trợ =====================
# kind: cách gọi API. "openai" = chuẩn OpenAI; "anthropic"; "gemini".
PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "label": "OpenAI · GPT", "kind": "openai",
        "base": "https://api.openai.com/v1", "default_model": "gpt-4o-mini",
        "models": ["gpt-4o", "gpt-4o-mini", "o3-mini"], "vision": True, "free": False,
    },
    "anthropic": {
        "label": "Anthropic · Claude", "kind": "anthropic",
        "base": "https://api.anthropic.com/v1", "default_model": "claude-3-5-sonnet-latest",
        "models": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"], "vision": True, "free": False,
    },
    "gemini": {
        "label": "Google · Gemini", "kind": "gemini",
        "base": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.0-flash",
        "models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
        "vision": True, "free": True,  # có gói free
    },
    "groq": {
        "label": "Groq · Llama (free)", "kind": "openai",
        "base": "https://api.groq.com/openai/v1", "default_model": "llama-3.3-70b-versatile",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"], "vision": False, "free": True,
    },
    "openrouter": {
        "label": "OpenRouter (nhiều model, có free)", "kind": "openai",
        "base": "https://openrouter.ai/api/v1", "default_model": "meta-llama/llama-3.3-70b-instruct",
        "models": ["meta-llama/llama-3.3-70b-instruct", "google/gemini-2.0-flash-exp:free"],
        "vision": True, "free": True,
    },
    "mistral": {
        "label": "Mistral", "kind": "openai",
        "base": "https://api.mistral.ai/v1", "default_model": "mistral-large-latest",
        "models": ["mistral-large-latest", "mistral-small-latest"], "vision": False, "free": False,
    },
    "deepseek": {
        "label": "DeepSeek", "kind": "openai",
        "base": "https://api.deepseek.com/v1", "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"], "vision": False, "free": False,
    },
    "xai": {
        "label": "xAI · Grok", "kind": "openai",
        "base": "https://api.x.ai/v1", "default_model": "grok-2-latest",
        "models": ["grok-2-latest", "grok-2-vision-latest"], "vision": True, "free": False,
    },
    "perplexity": {
        "label": "Perplexity · Sonar", "kind": "openai",
        "base": "https://api.perplexity.ai", "default_model": "sonar",
        "models": ["sonar", "sonar-pro"], "vision": False, "free": False,
    },
    "together": {
        "label": "Together AI", "kind": "openai",
        "base": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "models": ["meta-llama/Llama-3.3-70B-Instruct-Turbo",
                   "Qwen/Qwen2.5-72B-Instruct-Turbo"], "vision": False, "free": False,
    },
    "fireworks": {
        "label": "Fireworks AI", "kind": "openai",
        "base": "https://api.fireworks.ai/inference/v1",
        "default_model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "models": ["accounts/fireworks/models/llama-v3p3-70b-instruct"],
        "vision": False, "free": False,
    },
    "cerebras": {
        "label": "Cerebras (nhanh, free)", "kind": "openai",
        "base": "https://api.cerebras.ai/v1", "default_model": "llama-3.3-70b",
        "models": ["llama-3.3-70b", "llama-3.1-8b"], "vision": False, "free": True,
    },
    "moonshot": {
        "label": "Moonshot · Kimi", "kind": "openai",
        "base": "https://api.moonshot.ai/v1", "default_model": "moonshot-v1-8k",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k"], "vision": False, "free": False,
    },
    "qwen": {
        "label": "Alibaba · Qwen", "kind": "openai",
        "base": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max"], "vision": False, "free": False,
    },
    "nvidia": {
        "label": "NVIDIA NIM (free)", "kind": "openai",
        "base": "https://integrate.api.nvidia.com/v1",
        "default_model": "meta/llama-3.3-70b-instruct",
        "models": ["meta/llama-3.3-70b-instruct"], "vision": False, "free": True,
    },
    "cohere": {
        "label": "Cohere · Command", "kind": "openai",
        "base": "https://api.cohere.ai/compatibility/v1",
        "default_model": "command-r-plus-08-2024",
        "models": ["command-r-plus-08-2024", "command-r-08-2024"], "vision": False, "free": False,
    },
}

DEFAULT_SYSTEM = os.getenv(
    "SYSTEM_PROMPT",
    "Bạn là trợ lý AI của ứng dụng KENIOS. Trả lời hữu ích, chính xác, ưu tiên tiếng Việt.",
)

# ========================== Cơ sở dữ liệu ==========================
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT,
                phone TEXT,
                pw_hash TEXT NOT NULL,
                reset_token TEXT,
                reset_exp INTEGER,
                created_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS apikeys(
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                enc_key TEXT NOT NULL,
                PRIMARY KEY(user_id, provider)
            );
            CREATE TABLE IF NOT EXISTS conversations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT,
                provider TEXT,
                created_at INTEGER,
                updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS files(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                category TEXT,
                size INTEGER,
                data TEXT NOT NULL,
                created_at INTEGER
            );
            """
        )
    # Migration: thêm cột cho admin/ban/gói nếu DB cũ chưa có
    with db() as c:
        for col, ddl in [("is_admin", "INTEGER DEFAULT 0"),
                         ("banned", "INTEGER DEFAULT 0"),
                         ("plan", "TEXT DEFAULT 'free'")]:
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
            except Exception:
                pass
    # Seed tài khoản quản trị viên (đổi qua biến môi trường khi chạy thật)
    admin_user = os.getenv("ADMIN_USER", "kenios")
    admin_pass = os.getenv("ADMIN_PASS", "admin1999@")
    with db() as c:
        row = c.execute("SELECT id FROM users WHERE username=?", (admin_user,)).fetchone()
        if row:
            c.execute("UPDATE users SET is_admin=1, banned=0 WHERE id=?", (row["id"],))
        else:
            c.execute(
                "INSERT INTO users(username,pw_hash,is_admin,plan,created_at) VALUES(?,?,?,?,?)",
                (admin_user, hash_pw(admin_pass), 1, "pro", int(time.time())),
            )
            log.info("Đã tạo admin '%s' (hãy đổi mật khẩu sau khi đăng nhập!)", admin_user)
    log.info("DB sẵn sàng: %s", DB_PATH)


# ========================== Bảo mật ==========================
def hash_pw(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + "$" + dk.hex()


def verify_pw(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user_id: int) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + TOKEN_TTL}
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64u(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str) -> int:
    try:
        body, sig = token.split(".", 1)
        good = _b64u(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            raise ValueError("sai chữ ký")
        payload = json.loads(_b64u_dec(body))
        if payload["exp"] < time.time():
            raise ValueError("hết hạn")
        return int(payload["uid"])
    except Exception:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập không hợp lệ.")


def get_user(authorization: Optional[str] = Header(default=None)) -> sqlite3.Row:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Thiếu token đăng nhập.")
    uid = verify_token(authorization.split(" ", 1)[1])
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Tài khoản không tồn tại.")
    if row["banned"]:
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa.")
    return row


def get_admin(user=Depends(get_user)) -> sqlite3.Row:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên mới được phép.")
    return user


# ===================== Gọi nhà cung cấp AI =====================
def parse_image(image: str) -> tuple[str, str]:
    """Trả về (media_type, base64_thuần). Chấp nhận cả 'data:...;base64,xxx'."""
    if image.startswith("data:"):
        head, data = image.split(",", 1)
        m = re.search(r"data:(.*?);base64", head)
        return (m.group(1) if m else "image/jpeg"), data
    return "image/jpeg", image


async def call_provider(
    provider: str, api_key: str, model: Optional[str],
    history: list[dict[str, str]], user_text: str, image: Optional[str],
) -> str:
    """Gọi 1 AI và trả về văn bản trả lời. history: [{'role','content'}...]."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Không hỗ trợ AI '{provider}'.")
    p = PROVIDERS[provider]
    model = model or p["default_model"]
    kind = p["kind"]
    img = parse_image(image) if image else None

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        if kind == "openai":
            msgs = [{"role": "system", "content": DEFAULT_SYSTEM}]
            msgs += [{"role": m["role"], "content": m["content"]} for m in history]
            if img:
                media, data = img
                msgs.append({"role": "user", "content": [
                    {"type": "text", "text": user_text or ""},
                    {"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}},
                ]})
            else:
                msgs.append({"role": "user", "content": user_text})
            r = await client.post(
                f"{p['base']}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": msgs},
            )
            _raise_for_provider(r, provider)
            return r.json()["choices"][0]["message"]["content"]

        if kind == "anthropic":
            msgs = [{"role": m["role"], "content": m["content"]} for m in history]
            if img:
                media, data = img
                msgs.append({"role": "user", "content": [
                    {"type": "text", "text": user_text or ""},
                    {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
                ]})
            else:
                msgs.append({"role": "user", "content": user_text})
            r = await client.post(
                f"{p['base']}/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                json={"model": model, "max_tokens": 4096, "system": DEFAULT_SYSTEM, "messages": msgs},
            )
            _raise_for_provider(r, provider)
            return r.json()["content"][0]["text"]

        if kind == "gemini":
            contents = []
            for m in history:
                role = "model" if m["role"] == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m["content"]}]})
            parts: list[dict[str, Any]] = [{"text": user_text or ""}]
            if img:
                media, data = img
                parts.append({"inline_data": {"mime_type": media, "data": data}})
            contents.append({"role": "user", "parts": parts})
            r = await client.post(
                f"{p['base']}/models/{model}:generateContent?key={api_key}",
                json={
                    "contents": contents,
                    "systemInstruction": {"parts": [{"text": DEFAULT_SYSTEM}]},
                },
            )
            _raise_for_provider(r, provider)
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]

    raise HTTPException(status_code=500, detail="Lỗi cấu hình provider.")


def _raise_for_provider(r: httpx.Response, provider: str) -> None:
    if r.status_code < 400:
        return
    txt = r.text[:300]
    if r.status_code in (401, 403):
        raise HTTPException(
            status_code=400,
            detail=f"{provider}: API key sai hoặc không đủ quyền ({r.status_code}). "
                   f"Kiểm tra lại key trong app. Chi tiết: {txt}",
        )
    if r.status_code == 429:
        raise HTTPException(status_code=429, detail=f"{provider}: vượt hạn mức (429). Thử lại sau.")
    raise HTTPException(status_code=502, detail=f"{provider} lỗi {r.status_code}: {txt}")


def get_user_key(user_id: int, provider: str, inline: Optional[str]) -> str:
    if inline:
        return inline
    with db() as c:
        row = c.execute(
            "SELECT enc_key FROM apikeys WHERE user_id=? AND provider=?", (user_id, provider)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail=f"Chưa có API key cho '{provider}'. Hãy nhập key trong app.")
    return dec(row["enc_key"])


# ========================== FastAPI ==========================
app = FastAPI(title="KENIOS codebox", version="2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# --------- Models ---------
class RegisterIn(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    phone: Optional[str] = None


class LoginIn(BaseModel):
    username: str
    password: str


class ForgotIn(BaseModel):
    username: str


class ResetIn(BaseModel):
    token: str
    new_password: str


class ProfileIn(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    new_password: Optional[str] = None


class KeyIn(BaseModel):
    provider: str
    api_key: str


class ChatIn(BaseModel):
    provider: str
    message: str = ""
    image: Optional[str] = None
    model: Optional[str] = None
    conversation_id: Optional[int] = None
    api_key: Optional[str] = None      # cho phép gửi key trực tiếp (không bắt buộc)


class EnsembleIn(BaseModel):
    providers: list[str]
    message: str
    judge: Optional[str] = None        # AI dùng để tổng hợp (mặc định: cái đầu tiên)


# --------- Hệ thống / danh sách AI ---------
@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "time": int(time.time()), "version": "2.0"}


@app.get("/config")
def config() -> dict[str, Any]:
    # App đọc cái này sau khi nhập IP/URL để xác nhận kết nối + lấy danh sách AI
    return {"name": "KENIOS codebox", "providers": providers_public()}


def providers_public() -> list[dict[str, Any]]:
    return [
        {"id": k, "label": v["label"], "models": v["models"],
         "default_model": v["default_model"], "vision": v["vision"], "free": v["free"]}
        for k, v in PROVIDERS.items()
    ]


@app.get("/providers")
def providers() -> list[dict[str, Any]]:
    """Danh sách AI để app hiện ra cho người dùng chọn (kèm cờ free)."""
    return providers_public()


# --------- Tài khoản ---------
@app.post("/auth/register")
def register(b: RegisterIn) -> dict[str, Any]:
    if len(b.username) < 3 or len(b.password) < 6:
        raise HTTPException(status_code=400, detail="Username ≥3 ký tự, mật khẩu ≥6 ký tự.")
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (b.username,)).fetchone():
            raise HTTPException(status_code=409, detail="Username đã tồn tại.")
        cur = c.execute(
            "INSERT INTO users(username,email,phone,pw_hash,created_at) VALUES(?,?,?,?,?)",
            (b.username, b.email, b.phone, hash_pw(b.password), int(time.time())),
        )
        uid = cur.lastrowid
    return {"token": make_token(uid),
            "user": {"id": uid, "username": b.username, "email": b.email,
                     "phone": b.phone, "is_admin": False, "plan": "free"}}


@app.post("/auth/login")
def login(b: LoginIn) -> dict[str, Any]:
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (b.username,)).fetchone()
    if not row or not verify_pw(b.password, row["pw_hash"]):
        raise HTTPException(status_code=401, detail="Sai username hoặc mật khẩu.")
    if row["banned"]:
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa. Liên hệ quản trị viên.")
    return {"token": make_token(row["id"]),
            "user": {"id": row["id"], "username": row["username"],
                     "email": row["email"], "phone": row["phone"],
                     "is_admin": bool(row["is_admin"]), "plan": row["plan"]}}


@app.post("/auth/forgot-password")
def forgot(b: ForgotIn) -> dict[str, Any]:
    token = secrets.token_urlsafe(24)
    with db() as c:
        row = c.execute("SELECT id FROM users WHERE username=?", (b.username,)).fetchone()
        if row:
            c.execute("UPDATE users SET reset_token=?, reset_exp=? WHERE id=?",
                      (token, int(time.time()) + 1800, row["id"]))
    # Thực tế nên GỬI token này qua email/SMS. Ở đây trả về để app/test dùng.
    log.info("Reset token cho %s: %s", b.username, token)
    return {"message": "Nếu tài khoản tồn tại, mã đặt lại đã được tạo.", "reset_token": token}


@app.post("/auth/reset-password")
def reset(b: ResetIn) -> dict[str, Any]:
    if len(b.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu mới ≥6 ký tự.")
    with db() as c:
        row = c.execute("SELECT id,reset_exp FROM users WHERE reset_token=?", (b.token,)).fetchone()
        if not row or (row["reset_exp"] or 0) < time.time():
            raise HTTPException(status_code=400, detail="Mã đặt lại sai hoặc đã hết hạn.")
        c.execute("UPDATE users SET pw_hash=?, reset_token=NULL, reset_exp=NULL WHERE id=?",
                  (hash_pw(b.new_password), row["id"]))
    return {"message": "Đổi mật khẩu thành công."}


@app.post("/auth/update-profile")
def update_profile(b: ProfileIn, user=Depends(get_user)) -> dict[str, Any]:
    fields, vals = [], []
    if b.email is not None:
        fields.append("email=?"); vals.append(b.email)
    if b.phone is not None:
        fields.append("phone=?"); vals.append(b.phone)
    if b.new_password:
        if len(b.new_password) < 6:
            raise HTTPException(status_code=400, detail="Mật khẩu mới ≥6 ký tự.")
        fields.append("pw_hash=?"); vals.append(hash_pw(b.new_password))
    if not fields:
        raise HTTPException(status_code=400, detail="Không có gì để cập nhật.")
    vals.append(user["id"])
    with db() as c:
        c.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=?", vals)
    return {"message": "Cập nhật thành công."}


# --------- Quản lý API key (mã hóa khi lưu) ---------
@app.post("/keys")
def save_key(b: KeyIn, user=Depends(get_user)) -> dict[str, Any]:
    if b.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="AI không hỗ trợ.")
    with db() as c:
        c.execute(
            "INSERT INTO apikeys(user_id,provider,enc_key) VALUES(?,?,?) "
            "ON CONFLICT(user_id,provider) DO UPDATE SET enc_key=excluded.enc_key",
            (user["id"], b.provider, enc(b.api_key)),
        )
    return {"message": f"Đã lưu key cho {b.provider}."}


@app.get("/keys")
def list_keys(user=Depends(get_user)) -> list[dict[str, Any]]:
    with db() as c:
        rows = c.execute("SELECT provider FROM apikeys WHERE user_id=?", (user["id"],)).fetchall()
    return [{"provider": r["provider"], "configured": True} for r in rows]


@app.delete("/keys/{provider}")
def del_key(provider: str, user=Depends(get_user)) -> dict[str, Any]:
    with db() as c:
        c.execute("DELETE FROM apikeys WHERE user_id=? AND provider=?", (user["id"], provider))
    return {"message": f"Đã xóa key {provider}."}


# --------- Chat + lưu lịch sử ---------
def load_history(conversation_id: int, user_id: int) -> list[dict[str, str]]:
    with db() as c:
        own = c.execute("SELECT 1 FROM conversations WHERE id=? AND user_id=?",
                        (conversation_id, user_id)).fetchone()
        if not own:
            raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.")
        rows = c.execute(
            "SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def new_conversation(user_id: int, provider: str, title: str) -> int:
    now = int(time.time())
    with db() as c:
        cur = c.execute(
            "INSERT INTO conversations(user_id,title,provider,created_at,updated_at) VALUES(?,?,?,?,?)",
            (user_id, title[:60], provider, now, now),
        )
        return cur.lastrowid


def save_message(conversation_id: int, role: str, content: str) -> None:
    with db() as c:
        c.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
                  (conversation_id, role, content, int(time.time())))
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                  (int(time.time()), conversation_id))


@app.post("/chat")
async def chat(b: ChatIn, user=Depends(get_user)) -> dict[str, Any]:
    if not b.message and not b.image:
        raise HTTPException(status_code=400, detail="Thiếu 'message' hoặc 'image'.")
    key = get_user_key(user["id"], b.provider, b.api_key)

    conv_id = b.conversation_id or new_conversation(user["id"], b.provider, b.message or "Ảnh")
    history = load_history(conv_id, user["id"]) if b.conversation_id else []

    reply = await call_provider(b.provider, key, b.model, history, b.message, b.image)

    save_message(conv_id, "user", b.message or "[ảnh]")
    save_message(conv_id, "assistant", reply)
    return {"reply": reply, "conversation_id": conv_id, "provider": b.provider}


@app.post("/chat/ensemble")
async def ensemble(b: EnsembleIn, user=Depends(get_user)) -> dict[str, Any]:
    """Hỏi nhiều AI cùng lúc, rồi 1 AI 'trọng tài' tổng hợp ra câu trả lời tốt nhất."""
    if len(b.providers) < 2:
        raise HTTPException(status_code=400, detail="Cần ít nhất 2 AI để đối xứng.")

    async def one(prov: str):
        try:
            key = get_user_key(user["id"], prov, None)
            ans = await call_provider(prov, key, None, [], b.message, None)
            return prov, ans
        except HTTPException as e:
            return prov, f"[lỗi: {e.detail}]"

    results = await asyncio.gather(*[one(p) for p in b.providers])
    answers = {prov: ans for prov, ans in results}

    judge = b.judge or b.providers[0]
    judge_key = get_user_key(user["id"], judge, None)
    merged_prompt = (
        "Dưới đây là câu trả lời của nhiều AI cho cùng một câu hỏi. "
        "Hãy hợp nhất thành MỘT câu trả lời tốt nhất: chính xác, đầy đủ, thống nhất, "
        "loại bỏ mâu thuẫn.\n\nCÂU HỎI:\n" + b.message + "\n\nCÁC CÂU TRẢ LỜI:\n"
        + "\n\n".join(f"### {p}\n{a}" for p, a in answers.items())
    )
    best = await call_provider(judge, judge_key, None, [], merged_prompt, None)
    return {"best": best, "judge": judge, "answers": answers}


# --------- Lịch sử hội thoại ---------
@app.get("/conversations")
def list_conversations(user=Depends(get_user)) -> list[dict[str, Any]]:
    with db() as c:
        rows = c.execute(
            "SELECT id,title,provider,updated_at FROM conversations WHERE user_id=? "
            "ORDER BY updated_at DESC", (user["id"],)
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/conversations/{cid}")
def get_conversation(cid: int, user=Depends(get_user)) -> dict[str, Any]:
    msgs = load_history(cid, user["id"])
    return {"conversation_id": cid, "messages": msgs}


@app.delete("/conversations/{cid}")
def delete_conversation(cid: int, user=Depends(get_user)) -> dict[str, Any]:
    with db() as c:
        c.execute("DELETE FROM messages WHERE conversation_id=? AND conversation_id IN "
                  "(SELECT id FROM conversations WHERE user_id=?)", (cid, user["id"]))
        c.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (cid, user["id"]))
    return {"message": "Đã xóa hội thoại."}


# --------- Quản lý file (Màn hình 3) ---------
MAX_FILE_B64 = 11_000_000  # ~8MB nhị phân

class FileIn(BaseModel):
    name: str
    category: Optional[str] = None   # "image" | "code" | "document" | ...
    data_base64: str

@app.post("/files")
def upload_file(b: FileIn, user=Depends(get_user)) -> dict[str, Any]:
    if len(b.data_base64) > MAX_FILE_B64:
        raise HTTPException(status_code=413, detail="File quá lớn (giới hạn ~8MB).")
    size = (len(b.data_base64) * 3) // 4
    with db() as c:
        cur = c.execute(
            "INSERT INTO files(user_id,name,category,size,data,created_at) VALUES(?,?,?,?,?,?)",
            (user["id"], b.name, b.category or "other", size, b.data_base64, int(time.time())),
        )
        fid = cur.lastrowid
    return {"id": fid, "name": b.name, "size": size}

@app.get("/files")
def list_files(category: Optional[str] = None, user=Depends(get_user)) -> list[dict[str, Any]]:
    q = "SELECT id,name,category,size,created_at FROM files WHERE user_id=?"
    args: list[Any] = [user["id"]]
    if category and category != "all":
        q += " AND category=?"; args.append(category)
    q += " ORDER BY id DESC"
    with db() as c:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]

@app.get("/files/{fid}")
def download_file(fid: int, user=Depends(get_user)) -> dict[str, Any]:
    with db() as c:
        row = c.execute("SELECT name,category,data FROM files WHERE id=? AND user_id=?",
                        (fid, user["id"])).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy file.")
    return {"name": row["name"], "category": row["category"], "data_base64": row["data"]}

@app.delete("/files/{fid}")
def delete_file(fid: int, user=Depends(get_user)) -> dict[str, Any]:
    with db() as c:
        c.execute("DELETE FROM files WHERE id=? AND user_id=?", (fid, user["id"]))
    return {"message": "Đã xóa file."}


# --------- Quản trị viên (admin) ---------
class BanIn(BaseModel):
    banned: bool

class AdminPwIn(BaseModel):
    new_password: str

class PlanIn(BaseModel):
    plan: str   # "free" | "pro"

@app.get("/admin/users")
def admin_users(admin=Depends(get_admin)) -> list[dict[str, Any]]:
    with db() as c:
        rows = c.execute(
            "SELECT id,username,email,phone,is_admin,banned,plan,created_at FROM users ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]

@app.post("/admin/users/{uid}/ban")
def admin_ban(uid: int, b: BanIn, admin=Depends(get_admin)) -> dict[str, Any]:
    if uid == admin["id"]:
        raise HTTPException(status_code=400, detail="Không thể tự khóa chính mình.")
    with db() as c:
        c.execute("UPDATE users SET banned=? WHERE id=?", (1 if b.banned else 0, uid))
    return {"message": "Đã khóa." if b.banned else "Đã mở khóa."}

@app.post("/admin/users/{uid}/password")
def admin_set_password(uid: int, b: AdminPwIn, admin=Depends(get_admin)) -> dict[str, Any]:
    if len(b.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu ≥6 ký tự.")
    with db() as c:
        c.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_pw(b.new_password), uid))
    return {"message": "Đã đổi mật khẩu cho người dùng."}

@app.post("/admin/users/{uid}/plan")
def admin_set_plan(uid: int, b: PlanIn, admin=Depends(get_admin)) -> dict[str, Any]:
    if b.plan not in ("free", "pro"):
        raise HTTPException(status_code=400, detail="Gói không hợp lệ.")
    with db() as c:
        c.execute("UPDATE users SET plan=? WHERE id=?", (b.plan, uid))
    return {"message": f"Đã đặt gói '{b.plan}'."}


# --------- Giọng nói: audio -> văn bản (qua OpenAI Whisper-compatible) ---------
@app.post("/voice/transcribe")
async def transcribe(request: Request, user=Depends(get_user)) -> dict[str, Any]:
    """
    Nhận JSON: {"provider":"openai","audio_base64":"...","mime":"audio/m4a"}.
    Chuyển tiếp tới API audio/transcriptions của nhà cung cấp (OpenAI-compatible).
    """
    body = await request.json()
    prov = body.get("provider", "openai")
    audio_b64 = body.get("audio_base64")
    if not audio_b64:
        raise HTTPException(status_code=400, detail="Thiếu 'audio_base64'.")
    if PROVIDERS.get(prov, {}).get("kind") != "openai":
        raise HTTPException(status_code=400, detail="Phiên âm chỉ hỗ trợ provider kiểu OpenAI (vd: openai).")
    key = get_user_key(user["id"], prov, body.get("api_key"))
    audio = base64.b64decode(audio_b64)
    files = {"file": ("audio.m4a", audio, body.get("mime", "audio/m4a"))}
    data = {"model": body.get("model", "whisper-1")}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        r = await client.post(
            f"{PROVIDERS[prov]['base']}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"}, files=files, data=data,
        )
    _raise_for_provider(r, prov)
    return {"text": r.json().get("text", "")}


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    log.exception("Lỗi: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Lỗi server nội bộ."})


if __name__ == "__main__":
    import uvicorn
    init_db()
    log.info("KENIOS codebox chạy cổng %s | %d AI hỗ trợ", PORT, len(PROVIDERS))
    uvicorn.run(app, host="0.0.0.0", port=PORT)

# ==================================================================
# CHẠY 24/7 BẰNG systemd — tạo /etc/systemd/system/codebox.service:
#
#   [Unit]
#   Description=KENIOS codebox
#   After=network.target
#
#   [Service]
#   WorkingDirectory=/root/kenios
#   Environment=CODEBOX_SECRET=doi-thanh-chuoi-bi-mat
#   ExecStart=/root/kenios/venv/bin/uvicorn codebox:app --host 0.0.0.0 --port 8000
#   Restart=always
#
#   [Install]
#   WantedBy=multi-user.target
#
# Rồi: sudo systemctl daemon-reload && sudo systemctl enable --now codebox
# ==================================================================
