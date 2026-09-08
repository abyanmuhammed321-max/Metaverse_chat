from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import json
import sqlite3
import uuid
import asyncio
import os
import re
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

try:
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
    GOOGLE_AUTH_AVAILABLE = True
except ImportError:
    GOOGLE_AUTH_AVAILABLE = False

app = FastAPI(title="Metaverse_WhatsApp - Multimedia Edition")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com")

# ==================== DATABASE SETUP ====================
def init_db():
    conn = sqlite3.connect("metaverse_whatsapp.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            recipient TEXT,
            type TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            status TEXT,
            profile_pic TEXT,
            theme TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS google_accounts (
            google_sub TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS saved_contacts (
            username TEXT,
            contact TEXT,
            PRIMARY KEY (username, contact)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY,
            group_name TEXT,
            admin TEXT,
            members TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT,
            sender TEXT,
            type TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn

db_conn = init_db()
db_lock = asyncio.Lock()

@asynccontextmanager
async def db_transaction():
    """Serialize SQLite access because FastAPI WebSockets can run concurrently."""
    async with db_lock:
        yield db_conn.cursor()
        db_conn.commit()


# ==================== WEBSOCKET CONNECTION MANAGER ====================
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, username: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[username] = websocket
        await self.broadcast_user_list()

    def disconnect(self, username: str):
        if username in self.active_connections:
            del self.active_connections[username]

    async def broadcast_user_list(self):
        user_list = list(self.active_connections.keys())
        payload = {"type": "user_list", "users": user_list}
        dead = []
        for username, connection in list(self.active_connections.items()):
            try:
                await connection.send_text(json.dumps(payload))
            except Exception:
                dead.append(username)
        for username in dead:
            self.disconnect(username)

    async def send_personal_message(self, message: dict, recipient: str):
        connection = self.active_connections.get(recipient)
        if not connection:
            return False
        try:
            await connection.send_text(json.dumps(message))
            return True
        except Exception:
            self.disconnect(recipient)
            return False

    async def broadcast_to_group(self, group_id: str, message: dict, members: list):
        for member in members:
            if member in self.active_connections:
                await self.active_connections[member].send_text(json.dumps(message))

manager = ConnectionManager()

# ==================== API ENDPOINTS ====================

class UserProfile(BaseModel):
    username: str
    status: Optional[str] = None
    profile_pic: Optional[str] = None
    theme: Optional[str] = None

@app.post("/user/update")
async def update_user(profile: UserProfile):
    async with db_transaction() as cursor:
        cursor.execute("""
            INSERT INTO users (username, status, profile_pic, theme)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                status = COALESCE(?, status),
                profile_pic = COALESCE(?, profile_pic),
                theme = COALESCE(?, theme)
        """, (profile.username, profile.status, profile.profile_pic, profile.theme,
              profile.status, profile.profile_pic, profile.theme))
    return {"status": "success"}

@app.get("/user/{username}")
async def get_user(username: str):
    async with db_transaction() as cursor:
        cursor.execute("SELECT status, profile_pic, theme FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
    if row:
        return {"status": row[0], "profile_pic": row[1], "theme": row[2]}
    return {"status": "Hey there! I am using Metaverse WhatsApp", "profile_pic": None, "theme": "dark"}

class GoogleCredential(BaseModel):
    credential: str


def make_username(display_name: str, email: str, google_sub: str) -> str:
    """Create a readable username while keeping Google `sub` as the real account key."""
    base = email.split("@", 1)[0] if "@" in email else (display_name or "user")
    base = re.sub(r"[^a-zA-Z0-9_.-]", "", base).strip("._-")[:24] or "user"
    return base


@app.get("/config")
async def app_config():
    return {"google_client_id": GOOGLE_CLIENT_ID}


@app.post("/auth/google")
async def google_login(data: GoogleCredential):
    if not GOOGLE_AUTH_AVAILABLE:
        return {"status": "error", "message": "Google authentication dependency is missing. Install google-auth."}

    if not GOOGLE_CLIENT_ID or GOOGLE_CLIENT_ID.startswith("YOUR_GOOGLE_CLIENT_ID"):
        return {"status": "error", "message": "Configure GOOGLE_CLIENT_ID before using Google Sign-In."}

    try:
        info = id_token.verify_oauth2_token(
            data.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )

        google_sub = str(info.get("sub", ""))
        email = str(info.get("email", ""))
        display_name = str(info.get("name") or info.get("given_name") or email or "Google User")
        picture = str(info.get("picture") or "")

        if not google_sub:
            return {"status": "error", "message": "Google account ID was not provided."}

        async with db_transaction() as cursor:
            cursor.execute("SELECT username FROM google_accounts WHERE google_sub = ?", (google_sub,))
            existing = cursor.fetchone()

        if existing:
            username = existing[0]
        else:
            username = make_username(display_name, email, google_sub)

            async with db_transaction() as cursor:
                cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
                collision = cursor.fetchone()

            if collision:
                username = f"{username}_{google_sub[-6:]}"

            async with db_transaction() as cursor:
                cursor.execute(
                    "INSERT INTO google_accounts (google_sub, username, email) VALUES (?, ?, ?)",
                    (google_sub, username, email),
                )

        async with db_transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO users (username, status, profile_pic, theme)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    status = COALESCE(excluded.status, users.status),
                    profile_pic = COALESCE(excluded.profile_pic, users.profile_pic)
                """,
                (username, f"Google account · {display_name}", picture, "dark"),
            )

        return {
            "status": "success",
            "username": username,
            "display_name": display_name,
            "email": email,
            "profile_pic": picture,
        }

    except Exception as exc:
        return {"status": "error", "message": f"Google Sign-In verification failed: {exc}"}


class ContactAdd(BaseModel):
    username: str
    contact: str

@app.post("/contacts/add")
async def add_contact(data: ContactAdd):
    async with db_transaction() as cursor:
        cursor.execute("INSERT OR IGNORE INTO saved_contacts (username, contact) VALUES (?, ?)",
                       (data.username, data.contact))
    return {"status": "success"}

@app.get("/contacts/{username}")
async def get_saved_contacts(username: str):
    async with db_transaction() as cursor:
        cursor.execute("SELECT contact FROM saved_contacts WHERE username = ?", (username,))
        rows = cursor.fetchall()
    saved = [r[0] for r in rows]
    return {"contacts": saved}

class GroupCreate(BaseModel):
    group_name: str
    admin: str
    members: List[str]

@app.post("/groups/create")
async def create_group(group: GroupCreate):
    group_id = f"group_{uuid.uuid4().hex[:8]}"
    all_members = list(set(group.members + [group.admin]))
    async with db_transaction() as cursor:
        cursor.execute("INSERT INTO groups (group_id, group_name, admin, members) VALUES (?, ?, ?, ?)",
                       (group_id, group.group_name, group.admin, json.dumps(all_members)))
    return {"group_id": group_id, "group_name": group.group_name, "members": all_members}

@app.get("/groups/{username}")
async def get_user_groups(username: str):
    async with db_transaction() as cursor:
        cursor.execute("SELECT group_id, group_name, admin, members FROM groups")
        rows = cursor.fetchall()
    user_groups = []
    for r in rows:
        members = json.loads(r[3])
        if username in members:
            user_groups.append({"group_id": r[0], "group_name": r[1], "admin": r[2], "members": members})
    return {"groups": user_groups}

@app.get("/group-history/{group_id}")
async def get_group_history(group_id: str):
    async with db_transaction() as cursor:
        cursor.execute("SELECT id, sender, type, content FROM group_messages WHERE group_id = ? ORDER BY id ASC", (group_id,))
        rows = cursor.fetchall()
    history = [{"id": r[0], "sender": r[1], "type": r[2], "content": r[3]} for r in rows]
    return {"history": history}

@app.get("/history/{user}/{contact}")
async def get_history(user: str, contact: str):
    async with db_transaction() as cursor:
        cursor.execute("""
            SELECT id, sender, type, content FROM messages
            WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
            ORDER BY id ASC
        """, (user, contact, contact, user))
        rows = cursor.fetchall()
    history = [{"id": r[0], "sender": r[1], "type": r[2], "content": r[3]} for r in rows]
    return {"history": history}

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(username, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message_data = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Invalid JSON payload."
                }))
                continue
            
            msg_type = message_data.get("type")
            recipient_id = message_data.get("recipient_id")
            content = message_data.get("message")
            is_group = message_data.get("is_group", False)
            
            # Handle WebRTC signaling & Call requests directly
            if msg_type in ["call_request", "call_response", "offer", "answer", "ice_candidate", "end_call"]:
                message_data["sender_id"] = username
                await manager.send_personal_message(message_data, recipient_id)
                continue

            if is_group:
                async with db_transaction() as cursor:
                    cursor.execute("INSERT INTO group_messages (group_id, sender, type, content) VALUES (?, ?, ?, ?)",
                                   (recipient_id, username, msg_type, content))
                    msg_id = cursor.lastrowid

                    cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient_id,))
                    row = cursor.fetchone()
                if row:
                    members = json.loads(row[0])
                    payload = {
                        "id": msg_id,
                        "group_id": recipient_id,
                        "type": msg_type,
                        "sender_id": username,
                        "message": content,
                        "is_group": True
                    }
                    await manager.broadcast_to_group(recipient_id, payload, members)
                continue

            if msg_type in ["chat", "audio_note", "image"]:
                async with db_transaction() as cursor:
                    cursor.execute("INSERT INTO messages (sender, recipient, type, content) VALUES (?, ?, ?, ?)",
                                   (username, recipient_id, msg_type, content))
                    msg_id = cursor.lastrowid

                payload = {
                    "id": msg_id,
                    "type": msg_type,
                    "sender_id": username,
                    "message": content
                }
                await manager.send_personal_message(payload, recipient_id)
                
    except WebSocketDisconnect:
        manager.disconnect(username)
        await manager.broadcast_user_list()


@app.get("/")
async def home():
    return HTMLResponse(HTML_CONTENT.replace("__GOOGLE_CLIENT_ID__", GOOGLE_CLIENT_ID))


# ==================== FRONTEND UI ====================
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Metaverse WhatsApp - Multimedia & Call Edition</title>
    <link rel="icon" href="https://img.icons8.com/color/48/whatsapp--v1.png" type="image/png">
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <style>
        :root {
            --bg-primary: #080c14;
            --bg-secondary: #111827;
            --bg-panel: #1f2937;
            --accent: #00f2fe;
            --accent-gradient: linear-gradient(135deg, #00f2fe, #3b82f6);
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --border: #374151;
            --outgoing: #059669;
        }

        body.theme-light {
            --bg-primary: #f0f2f5;
            --bg-secondary: #ffffff;
            --bg-panel: #ffffff;
            --accent: #00a884;
            --accent-gradient: linear-gradient(135deg, #00a884, #005c4b);
            --text-main: #111827;
            --text-muted: #6b7280;
            --border: #e5e7eb;
            --outgoing: #00a884;
        }

        body.theme-dark {
            --bg-primary: #080c14;
            --bg-secondary: #111827;
            --bg-panel: #1f2937;
            --accent: #00f2fe;
            --accent-gradient: linear-gradient(135deg, #00f2fe, #3b82f6);
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --border: #374151;
            --outgoing: #059669;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }
        body { background: var(--bg-primary); height: 100vh; display: flex; justify-content: center; align-items: center; color: var(--text-main); overflow: hidden; }
        .hidden { display: none !important; }
        
        #app-container { width: 98%; max-width: 1500px; height: 95vh; background: var(--bg-secondary); border: 1px solid var(--border); display: flex; box-shadow: 0 0 40px rgba(0, 0, 0, 0.5); border-radius: 18px; overflow: hidden; position: relative; }
        
        #login-screen { position: absolute; inset: 0; background: var(--bg-primary); display: flex; justify-content: center; align-items: center; z-index: 200; padding: 15px; }
        #login-box { background: var(--bg-panel); border: 1px solid var(--border); padding: 40px 30px; border-radius: 20px; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.3); width: 100%; max-width: 440px; }
        #login-box h1 { color: var(--accent); margin-bottom: 8px; font-size: 26px; font-weight: 700; }
        #login-box p { color: var(--text-muted); font-size: 13px; margin-bottom: 25px; }
        
        .google-btn-wrapper { display: flex; justify-content: center; margin-bottom: 20px; }
        .divider { display: flex; align-items: center; text-align: center; color: var(--text-muted); font-size: 12px; margin: 18px 0; }
        .divider::before, .divider::after { content: ''; flex: 1; border-bottom: 1px solid var(--border); }
        .divider::before { margin-right: .5em; }
        .divider::after { margin-left: .5em; }

        #login-box input { width: 100%; padding: 12px 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 10px; color: var(--text-main); font-size: 14px; outline: none; margin-bottom: 14px; text-align: center; }
        #login-box input:focus { border-color: var(--accent); }
        #login-box button.manual-login { width: 100%; padding: 12px; background: var(--accent-gradient); color: #fff; border: none; border-radius: 10px; font-weight: bold; font-size: 14px; cursor: pointer; transition: 0.2s; }

        .sidebar { width: 35%; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; height: 100%; }
        .sidebar-header { padding: 16px 20px; background: var(--bg-secondary); display: flex; align-items: center; justify-content: space-between; height: 75px; border-bottom: 1px solid var(--border); }
        .my-profile { font-weight: 600; color: var(--accent); font-size: 14px; display: flex; align-items: center; gap: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 170px; cursor: pointer; }
        .contact-avatar { width: 48px; height: 48px; border-radius: 50%; background: var(--accent-gradient); color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 18px; margin-right: 14px; position: relative; flex-shrink: 0; overflow: hidden; }
        .contact-avatar img { width: 100%; height: 100%; object-fit: cover; }
        .online-dot { width: 12px; height: 12px; background: #10b981; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        .offline-dot { width: 12px; height: 12px; background: #6b7280; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        
        .sidebar-toolbar { padding: 12px 18px; background: var(--bg-panel); border-bottom: 1px solid var(--border); display: flex; gap: 8px; }
        .sidebar-toolbar input { flex: 1; padding: 10px 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 8px; color: var(--text-main); font-size: 13px; outline: none; }
        .sidebar-toolbar input:focus { border-color: var(--accent); }
        
        .contacts-list { flex: 1; overflow-y: auto; }
        .contact-item { display: flex; align-items: center; padding: 14px 18px; border-bottom: 1px solid var(--border); cursor: pointer; transition: 0.2s; position: relative; }
        .contact-item:hover, .contact-item.active { background: var(--bg-secondary); border-left: 4px solid var(--accent); }
        .contact-details h4 { font-size: 15px; color: var(--text-main); font-weight: 500; }
        .contact-details p { font-size: 12px; color: var(--accent); margin-top: 3px; }

        .chat-panel { flex: 1; display: flex; flex-direction: column; background: var(--bg-primary); position: relative; height: 100%; }
        .chat-header { height: 75px; background: var(--bg-secondary); padding: 12px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); }
        .active-chat-info { display: flex; align-items: center; gap: 12px; cursor: pointer; }
        .header-actions { display: flex; align-items: center; gap: 8px; }
        
        .header-btn { background: var(--bg-panel); color: var(--text-main); border: 1px solid var(--border); padding: 7px 12px; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 500; transition: 0.2s; white-space: nowrap; }
        .header-btn:hover { border-color: var(--accent); color: var(--accent); }
        
        .chat-messages { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; }
        
        .message { max-width: 75%; padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 22px; word-wrap: break-word; position: relative; box-shadow: 0 2px 5px rgba(0,0,0,0.1); display: flex; align-items: flex-start; gap: 10px; }
        .message.incoming { background: var(--bg-panel); border: 1px solid var(--border); align-self: flex-start; border-top-left-radius: 2px; color: var(--text-main); }
        .message.outgoing { background: var(--outgoing); align-self: flex-end; border-top-right-radius: 2px; color: white; }
        
        .msg-body { flex: 1; }
        .msg-ticks { font-size: 11px; float: right; margin-left: 10px; margin-top: 4px; color: rgba(255,255,255,0.8); }

        .chat-input-area { min-height: 75px; background: var(--bg-secondary); padding: 12px 18px; display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--border); }
        .chat-input-area input { flex: 1; padding: 12px 14px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg-panel); color: var(--text-main); font-size: 14px; outline: none; }
        .chat-input-area input:focus { border-color: var(--accent); }
        .action-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: var(--text-muted); padding: 4px; transition: 0.2s; }
        .action-btn:hover { color: var(--accent); }
        .action-btn.recording { color: #ef4444; animation: pulse 1.2s infinite; }

        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }

        .modal-overlay { position: absolute; inset: 0; background: rgba(0,0,0,0.7); z-index: 300; display: flex; justify-content: center; align-items: center; backdrop-filter: blur(4px); padding: 20px; }
        .modal-content { background: var(--bg-panel); border: 1px solid var(--border); padding: 25px; border-radius: 16px; width: 100%; max-width: 420px; box-shadow: 0 15px 35px rgba(0,0,0,0.5); }
        .modal-content h3 { color: var(--accent); margin-bottom: 16px; font-size: 18px; }
        .modal-content label { font-size: 12px; color: var(--text-muted); display: block; margin-bottom: 4px; margin-top: 12px; }
        .modal-content input, .modal-content textarea { width: 100%; padding: 10px 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 8px; color: var(--text-main); font-size: 13px; outline: none; }
        .sel-btn { background: var(--bg-secondary); border: 1px solid var(--border); color: var(--text-main); padding: 10px 16px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px; transition: 0.2s; }
        .sel-btn:hover { border-color: var(--accent); color: var(--accent); }

        .profile-trigger { cursor: pointer; transition: transform .18s ease, box-shadow .18s ease; }
        .profile-trigger:hover { transform: scale(1.04); box-shadow: 0 0 0 3px rgba(0,242,254,.10); }
        .google-note { font-size: 11px; line-height: 1.5; color: var(--text-muted); margin-top: 12px; }

        /* Call Screen Modal */
        #call-modal { position: absolute; inset: 0; background: rgba(0,0,0,0.92); z-index: 400; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .call-container { width: 90%; max-width: 800px; height: 75vh; background: var(--bg-panel); border-radius: 16px; border: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; position: relative; }
        .call-videos { flex: 1; display: flex; background: #000; position: relative; justify-content: center; align-items: center; }
        #remoteVideo { width: 100%; height: 100%; object-fit: cover; }
        #localVideo { width: 140px; height: 100px; position: absolute; bottom: 15px; right: 15px; border-radius: 8px; border: 2px solid var(--accent); object-fit: cover; background: #111; }
        .call-controls { height: 75px; background: var(--bg-secondary); display: flex; justify-content: center; align-items: center; gap: 20px; }
        .call-btn { padding: 10px 22px; border-radius: 50px; border: none; font-weight: bold; cursor: pointer; font-size: 14px; }
        .call-btn.end { background: #ef4444; color: white; }
        .call-btn.accept { background: #10b981; color: white; }

        @media (max-width: 768px) {
            #app-container { width: 100%; height: 100vh; height: 100dvh; border-radius: 0; border: none; }
            .sidebar { width: 100%; display: flex; }
            .chat-panel { width: 100%; display: none; }
            #app-container.mobile-chat-open .sidebar { display: none; }
            #app-container.mobile-chat-open .chat-panel { display: flex; }
            #backToContactsBtn { display: inline-block !important; }
        }

        /* ===== ADVANCED UI LAYER ===== */
        :root {
            --glass: rgba(255,255,255,.055);
            --glass-strong: rgba(255,255,255,.09);
            --shadow-lg: 0 24px 70px rgba(0,0,0,.38);
            --radius-lg: 22px;
            --radius-md: 14px;
            --ease: cubic-bezier(.2,.8,.2,1);
        }

        html { color-scheme: dark; }
        body {
            background:
                radial-gradient(circle at 12% 12%, rgba(0,242,254,.12), transparent 30%),
                radial-gradient(circle at 88% 82%, rgba(59,130,246,.13), transparent 34%),
                var(--bg-primary);
        }

        #app-container {
            width: min(1500px, 96vw);
            height: min(920px, 94vh);
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow-lg);
            backdrop-filter: blur(18px);
            animation: appIn .45s var(--ease);
        }

        @keyframes appIn {
            from { opacity: 0; transform: translateY(14px) scale(.985); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }

        .sidebar,
        .chat-panel,
        .chat-header,
        .sidebar-header,
        .chat-input-area,
        .modal-content {
            backdrop-filter: blur(18px);
        }

        .sidebar-header {
            box-shadow: 0 1px 0 rgba(255,255,255,.025);
        }

        .header-btn, .sel-btn, .action-btn {
            transition: transform .18s var(--ease), border-color .18s, background .18s, color .18s, box-shadow .18s;
        }

        .header-btn:hover, .sel-btn:hover {
            transform: translateY(-1px);
            box-shadow: 0 8px 22px rgba(0,0,0,.18);
        }

        .contact-item {
            margin: 5px 8px;
            padding: 12px 13px;
            border: 1px solid transparent;
            border-radius: 13px;
        }

        .contact-item:hover, .contact-item.active {
            border-left: 1px solid var(--accent);
            background: linear-gradient(90deg, var(--glass-strong), transparent);
            box-shadow: inset 0 0 20px rgba(0,242,254,.025);
        }

        .contact-avatar {
            box-shadow: 0 7px 20px rgba(0,0,0,.18);
        }

        .chat-messages {
            background:
                radial-gradient(circle at 20% 10%, rgba(0,242,254,.025), transparent 24%),
                radial-gradient(circle at 80% 90%, rgba(59,130,246,.035), transparent 28%);
            scrollbar-width: thin;
        }

        .message {
            border-radius: 16px;
            animation: messageIn .22s var(--ease);
        }

        @keyframes messageIn {
            from { opacity: 0; transform: translateY(5px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .message.outgoing {
            background: linear-gradient(135deg, #059669, #047857);
            box-shadow: 0 8px 24px rgba(5,150,105,.16);
        }

        .message.incoming {
            background: linear-gradient(135deg, rgba(31,41,55,.94), rgba(17,24,39,.94));
        }

        .chat-input-area {
            padding: 14px 18px;
            gap: 12px;
        }

        .chat-input-area input {
            height: 48px;
            border-radius: 15px;
            background: var(--glass);
        }

        .action-btn {
            width: 42px;
            height: 42px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 12px;
        }

        .action-btn:hover {
            background: var(--glass-strong);
            transform: translateY(-1px);
        }

        .modal-overlay {
            background: rgba(0,0,0,.72);
            backdrop-filter: blur(10px);
        }

        .modal-content {
            border-radius: 20px;
            box-shadow: 0 30px 90px rgba(0,0,0,.45);
            animation: modalIn .25s var(--ease);
        }

        @keyframes modalIn {
            from { opacity: 0; transform: scale(.96) translateY(8px); }
            to { opacity: 1; transform: scale(1) translateY(0); }
        }

        #call-modal {
            background: radial-gradient(circle at center, rgba(0,242,254,.08), rgba(0,0,0,.94) 55%);
            backdrop-filter: blur(12px);
        }

        .call-container {
            width: min(1100px, 94vw);
            height: min(760px, 82vh);
            border-radius: 22px;
            box-shadow: 0 35px 100px rgba(0,0,0,.6);
        }

        .call-videos {
            background: #030508;
        }

        #remoteVideo {
            background: radial-gradient(circle, #111827, #030508);
        }

        #localVideo {
            width: 180px;
            height: 125px;
            position: absolute;
            bottom: 18px;
            right: 18px;
            border-radius: 14px;
            border: 2px solid var(--accent);
            object-fit: cover;
            background: #111;
            box-shadow: 0 12px 30px rgba(0,0,0,.45);
        }

        .call-controls {
            height: 82px;
        }

        .call-btn {
            min-width: 110px;
            box-shadow: 0 8px 24px rgba(0,0,0,.2);
            transition: transform .18s var(--ease);
        }

        .call-btn:hover { transform: translateY(-2px); }

        input:focus, textarea:focus {
            box-shadow: 0 0 0 3px rgba(0,242,254,.08);
        }

        ::selection { background: rgba(0,242,254,.25); }

        @media (max-width: 768px) {
            #app-container { width: 100%; height: 100dvh; }
            .contact-item { margin: 4px 6px; }
            .chat-messages { padding: 14px; }
            .message { max-width: 88%; }
            #localVideo { width: 125px; height: 90px; right: 10px; bottom: 10px; }
            .call-container { width: 100%; height: 100%; border-radius: 0; }
        }

        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
                animation-duration: .01ms !important;
                transition-duration: .01ms !important;
            }
        }

    </style>
</head>
<body class="theme-dark">

    <div id="app-container">
        <!-- Login Screen -->
        <div id="login-screen">
            <div id="login-box">
                <h1>⚡ Metaverse</h1>
                <p>Quantum Multimedia Suite</p>
                
                <div class="google-btn-wrapper">
                    <div id="g_id_onload"
                         data-client_id="358332042325-3s7o118sjfv1qug4r6qlmf534083ti10.apps.googleusercontent.com"
                         data-callback="handleGoogleLogin"
                         data-auto_select="true">
                    </div>
                    <div class="g_id_signin" data-type="standard" data-shape="pill" data-theme="filled_black" data-size="large"></div>
                </div>
                <div class="google-note">Google securely verifies your ID token on the server before creating your account.</div>

                <div class="divider">or quick manual access</div>
                
                <input type="text" id="loginUsernameInput" placeholder="Enter custom username..." onkeypress="handleLoginKey(event)">
                <button class="manual-login" onclick="performManualLogin()">Initialize Session</button>
            </div>
        </div>

        <!-- Sidebar -->
        <div class="sidebar">
            <div class="sidebar-header">
                <div class="my-profile" onclick="openSettingsModal()">
                    <div class="contact-avatar" id="myAvatarDisplay" style="width: 36px; height: 36px; font-size: 14px; margin-right: 0;">⚡</div>
                    <span id="my-profile-display">Node</span>
                </div>
                <div style="display: flex; gap: 6px;">
                    <button class="header-btn" onclick="openGroupModal()" title="New Group">👥 Group</button>
                    <button class="header-btn" onclick="openSettingsModal()" title="Settings">⚙️</button>
                    <button class="header-btn" onclick="logout()" title="Logout" style="font-size: 11px;">Logout</button>
                </div>
            </div>
            <div class="sidebar-toolbar">
                <input type="text" id="searchContactInput" placeholder="Search saved contacts..." oninput="filterContacts()">
            </div>
            <div class="contacts-list" id="contactsListContainer"></div>
        </div>

        <!-- Chat Panel -->
        <div class="chat-panel">
            <div class="chat-header">
                <div class="active-chat-info" onclick="openContactProfile()">
                    <button class="header-btn hidden" id="backToContactsBtn" onclick="returnToSidebar(event)">⬅️</button>
                    <div class="contact-avatar" id="activeChatAvatar">?</div>
                    <div>
                        <h4 id="activeChatTitle" style="font-size: 15px; font-weight: 500;">Select Contact</h4>
                        <p id="activeChatStatus" style="font-size: 11px; color: var(--accent);">Ready</p>
                    </div>
                </div>
                <div class="header-actions" id="chatHeaderActions">
                    <button class="header-btn" onclick="startCall('voice')" title="Voice Call">📞</button>
                    <button class="header-btn" onclick="startCall('video')" title="Video Call">📹</button>
                </div>
            </div>

            <div class="chat-messages" id="chatMessagesContainer">
                <div style="text-align: center; margin: auto; color: var(--text-muted); font-size: 13px;">
                    <p>🔒 Select a contact or group to begin secure communication.</p>
                </div>
            </div>

            <div class="chat-input-area">
                <label class="action-btn" title="Attach Image">📎<input type="file" id="imageInput" accept="image/*" style="display:none;" onchange="sendImage(event)"></label>
                <button class="action-btn" id="voiceRecordBtn" title="Hold/Click to record voice note" onclick="toggleVoiceRecording()">🎤</button>
                <input type="text" id="messageInput" placeholder="Type a message..." onkeypress="handleKey(event)" disabled>
                <button class="action-btn" onclick="sendMessage()" style="color: var(--accent); font-size: 22px;" title="Send">➤</button>
            </div>
        </div>

        <!-- Settings Modal -->
        <div id="settings-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <h3>⚙️ Settings & Profile</h3>
                <label>Display Name</label>
                <input type="text" id="settingsNameInput">
                
                <label>About / Status</label>
                <textarea id="settingsStatusInput" rows="2"></textarea>
                
                <label>Profile Picture</label>
                <input type="file" id="settingsPicInput" accept="image/*" style="padding: 6px; background: var(--bg-secondary);">

                <label>Theme Mode</label>
                <div style="display: flex; gap: 10px; margin-top: 6px; margin-bottom: 20px;">
                    <button class="sel-btn" style="flex:1;" onclick="setTheme('dark')">🌙 Dark Mode</button>
                    <button class="sel-btn" style="flex:1;" onclick="setTheme('light')">☀️ Light Mode</button>
                </div>

                <div style="display: flex; gap: 10px;">
                    <button class="sel-btn" style="flex: 1; background: var(--accent-gradient); color: white;" onclick="saveSettings()">Save Changes</button>
                    <button class="sel-btn" style="flex: 1;" onclick="closeSettingsModal()">Cancel</button>
                </div>
            </div>
        </div>

        <!-- Contact Profile Modal -->
        <div id="contact-profile-modal" class="modal-overlay hidden">
            <div class="modal-content" style="text-align: center;">
                <div class="contact-avatar" id="modalProfileAvatar" style="width: 80px; height: 80px; font-size: 32px; margin: 0 auto 15px auto;">?</div>
                <h3 id="modalProfileName" style="margin-bottom: 5px;">Contact Name</h3>
                <p id="modalProfileStatus" style="color: var(--text-muted); font-size: 13px; margin-bottom: 20px;">Status here...</p>
                <div id="addToContactsBtnWrapper">
                    <button class="sel-btn" style="width: 100%; background: var(--accent-gradient); color: white; margin-bottom: 10px;" onclick="addCurrentContactPermanent()">➕ Add to Contacts</button>
                </div>
                <button class="sel-btn" style="width: 100%;" onclick="closeContactProfile()">Close</button>
            </div>
        </div>

        <!-- Create Group Modal -->
        <div id="group-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <h3>👥 Create New Group</h3>
                <label>Group Name</label>
                <input type="text" id="groupNameInput" placeholder="Enter group name...">
                
                <label>Select Members</label>
                <div id="groupMembersList" style="max-height: 180px; overflow-y: auto; margin-top: 6px; margin-bottom: 16px; border: 1px solid var(--border); border-radius: 8px; padding: 10px;"></div>

                <div style="display: flex; gap: 10px;">
                    <button class="sel-btn" style="flex: 1; background: var(--accent-gradient); color: white;" onclick="createGroupSubmit()">Create Group</button>
                    <button class="sel-btn" style="flex: 1;" onclick="closeGroupModal()">Cancel</button>
                </div>
            </div>
        </div>

        <!-- Call Modal -->
        <div id="call-modal" class="hidden">
            <div class="call-container">
                <div style="padding: 15px 20px; background: var(--bg-secondary); border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;">
                    <h4 id="callStatusTitle" style="color: var(--accent);">Secure Call in Progress</h4>
                    <span id="callPartnerLabel" style="font-size: 13px; color: var(--text-muted);"></span>
                </div>
                <div class="call-videos">
                    <video id="remoteVideo" autoplay playsinline></video>
                    <video id="localVideo" autoplay playsinline muted></video>
                </div>
                <div class="call-controls">
                    <button class="call-btn end" onclick="endCall()">End Call</button>
                </div>
            </div>
        </div>

        <!-- Incoming Call Modal -->
        <div id="incoming-call-modal" class="modal-overlay hidden">
            <div class="modal-content" style="text-align: center;">
                <h3 id="incomingCallerTitle" style="margin-bottom: 10px;">Incoming Call</h3>
                <p id="incomingCallTypeDesc" style="color: var(--text-muted); margin-bottom: 25px;">Incoming video/voice call...</p>
                <div style="display: flex; gap: 12px;">
                    <button class="sel-btn" style="flex: 1; background: #10b981; color: white;" onclick="acceptIncomingCall()">Accept</button>
                    <button class="sel-btn" style="flex: 1; background: #ef4444; color: white;" onclick="rejectIncomingCall()">Reject</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let ws;
        let currentUser = localStorage.getItem("metaverse_user") || null;
        let userStatus = "Hey there! I am using Metaverse WhatsApp";
        let userProfilePic = "";
        let currentTheme = "dark";
        
        let onlineUsers = [];
        let savedContacts = [];
        let userGroups = [];
        let activeContact = null;
        let isGroupActive = false;
        let chatHistories = {};

        // Audio recording state
        let mediaRecorder;
        let audioChunks = [];
        let isRecording = false;

        // WebRTC Call state
        let peerConnection;
        let localStream;
        let remoteStream;
        let currentCallType = null;
        let incomingCallData = null;
        let activeCallPartner = null;

        const rtcConfig = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

        window.onload = async function() {
            if (currentUser) {
                await fetchUserData(currentUser);
                initializeUserSession(currentUser);
            }
        };

        async function fetchUserData(username) {
            try {
                const res = await fetch(`/user/${encodeURIComponent(username)}`);
                const data = await res.json();
                userStatus = data.status || userStatus;
                userProfilePic = data.profile_pic || "";
                currentTheme = data.theme || "dark";
                setTheme(currentTheme, false);
            } catch (err) {
                console.error("Failed to fetch user data", err);
            }
        }

        async function handleGoogleLogin(response) {
            if (!response || !response.credential) {
                alert("Google did not return a credential.");
                return;
            }
            try {
                const res = await fetch("/auth/google", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ credential: response.credential })
                });
                const data = await res.json();
                if (data.status !== "success") {
                    alert(data.message || "Google Sign-In failed.");
                    return;
                }
                currentUser = data.username;
                userStatus = data.display_name ? `Google account · ${data.display_name}` : userStatus;
                userProfilePic = data.profile_pic || "";
                localStorage.setItem("metaverse_user", currentUser);
                localStorage.setItem("metaverse_google_signed_in", "1");
                await fetchUserData(currentUser);
                initializeUserSession(currentUser);
            } catch (err) {
                console.error(err);
                alert("Unable to complete Google Sign-In.");
            }
        }

        function handleLoginKey(e) { if (e.key === "Enter") performManualLogin(); }

        async function performManualLogin() {
            const val = document.getElementById("loginUsernameInput").value.trim();
            if (!val) { alert("Please enter a username."); return; }
            await fetchUserData(val);
            initializeUserSession(val);
        }

        async function initializeUserSession(username) {
            currentUser = username;
            localStorage.setItem("metaverse_user", currentUser);

            document.getElementById("my-profile-display").innerText = currentUser;
            if (userProfilePic) {
                document.getElementById("myAvatarDisplay").innerHTML = `<img src="${userProfilePic}">`;
            } else {
                document.getElementById("myAvatarDisplay").innerText = currentUser.charAt(0).toUpperCase();
            }

            document.getElementById("login-screen").classList.add("hidden");
            connectWebSocket();
            await fetchSavedContacts();
            await fetchUserGroups();
        }

        function logout() {
            localStorage.removeItem("metaverse_user");
            location.reload();
        }

        function setTheme(themeName, save = true) {
            currentTheme = themeName;
            document.body.className = `theme-${themeName}`;
            if (save && currentUser) saveUserProfileToBackend();
        }

        async function saveUserProfileToBackend() {
            await fetch("/user/update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: currentUser, status: userStatus, profile_pic: userProfilePic, theme: currentTheme })
            });
        }

        function openSettingsModal() {
            document.getElementById("settingsNameInput").value = currentUser;
            document.getElementById("settingsStatusInput").value = userStatus;
            document.getElementById("settings-modal").classList.remove("hidden");
        }

        function closeSettingsModal() { document.getElementById("settings-modal").classList.add("hidden"); }

        async function saveSettings() {
            const newName = document.getElementById("settingsNameInput").value.trim();
            const newStatus = document.getElementById("settingsStatusInput").value.trim();
            const picFile = document.getElementById("settingsPicInput").files[0];

            if (newName && newName !== currentUser) {
                currentUser = newName;
                localStorage.setItem("metaverse_user", currentUser);
            }
            if (newStatus) userStatus = newStatus;

            if (picFile) {
                const reader = new FileReader();
                reader.onload = async function() {
                    userProfilePic = reader.result;
                    await finishSavingSettings();
                };
                reader.readAsDataURL(picFile);
            } else {
                await finishSavingSettings();
            }
        }

        async function finishSavingSettings() {
            await saveUserProfileToBackend();
            document.getElementById("my-profile-display").innerText = currentUser;
            if (userProfilePic) document.getElementById("myAvatarDisplay").innerHTML = `<img src="${userProfilePic}">`;
            closeSettingsModal();
            alert("Settings saved successfully!");
        }

        async function fetchSavedContacts() {
            try {
                const res = await fetch(`/contacts/${encodeURIComponent(currentUser)}`);
                const data = await res.json();
                savedContacts = data.contacts;
                renderContacts();
            } catch (err) { console.error("Failed to load contacts", err); }
        }

        async function fetchUserGroups() {
            try {
                const res = await fetch(`/groups/${encodeURIComponent(currentUser)}`);
                const data = await res.json();
                userGroups = data.groups;
                renderContacts();
            } catch (err) { console.error("Failed to load groups", err); }
        }

        let reconnectTimer = null;

        function connectWebSocket() {
            if (!currentUser) return;
            const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
            ws = new WebSocket(`${wsProtocol}${window.location.host}/ws/${encodeURIComponent(currentUser)}`);

            ws.onopen = () => {
                console.log("WebSocket connected");
                if (reconnectTimer) {
                    clearTimeout(reconnectTimer);
                    reconnectTimer = null;
                }
            };

            ws.onclose = () => {
                if (currentUser) {
                    reconnectTimer = setTimeout(connectWebSocket, 1800);
                }
            };

            ws.onerror = () => ws.close();
            
            ws.onmessage = async function(event) {
                const data = JSON.parse(event.data);
                
                if (data.type === "user_list") {
                    onlineUsers = data.users.filter(u => u !== currentUser);
                    renderContacts();
                } else if (data.is_group) {
                    const gId = data.group_id;
                    if (!chatHistories[gId]) chatHistories[gId] = [];
                    chatHistories[gId].push({ id: data.id, sender: data.sender_id, type: data.type, content: data.message });
                    if (activeContact === gId) renderMessages();
                } else if (data.type === "call_request") {
                    incomingCallData = data;
                    document.getElementById("incomingCallerTitle").innerText = `${data.sender_id} is calling...`;
                    document.getElementById("incomingCallTypeDesc").innerText = `Incoming ${data.call_type} call`;
                    document.getElementById("incoming-call-modal").classList.remove("hidden");
                } else if (data.type === "call_response") {
                    if (data.accepted) {
                        activeCallPartner = data.sender_id;
                        document.getElementById("callPartnerLabel").innerText = `Connected with ${activeCallPartner}`;
                        await setupWebRTCConnection(true);
                    } else {
                        alert("Call rejected.");
                        closeCallModals();
                    }
                } else if (data.type === "offer") {
                    if (!peerConnection) await setupWebRTCConnection(false);
                    await peerConnection.setRemoteDescription(new RTCSessionDescription(data.offer));
                    const answer = await peerConnection.createAnswer();
                    await peerConnection.setLocalDescription(answer);
                    ws.send(JSON.stringify({ type: "answer", recipient_id: data.sender_id, answer: answer }));
                } else if (data.type === "answer") {
                    await peerConnection.setRemoteDescription(new RTCSessionDescription(data.answer));
                } else if (data.type === "ice_candidate") {
                    if (peerConnection && data.candidate) {
                        await peerConnection.addIceCandidate(new RTCIceCandidate(data.candidate));
                    }
                } else if (data.type === "end_call") {
                    closeCallModals();
                } else {
                    const sender = data.sender_id;
                    if (!chatHistories[sender]) chatHistories[sender] = [];
                    chatHistories[sender].push({ id: data.id, sender: sender, type: data.type, content: data.message });
                    if (activeContact === sender) renderMessages();
                    renderContacts();
                }
            };
        }

        function escapeHtml(value) {
            return String(value ?? "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#39;");
        }

        function escapeJs(value) {
            return String(value ?? "")
                .replace(/\\/g, "\\\\")
                .replace(/'/g, "\\'")
                .replace(/\n/g, "\\n")
                .replace(/\r/g, "\\r");
        }

        function renderContacts(filter = "") {
            const container = document.getElementById("contactsListContainer");
            container.innerHTML = "";
            
            userGroups.forEach(grp => {
                if (!grp.group_name.toLowerCase().includes(filter.toLowerCase())) return;
                const isActive = activeContact === grp.group_id ? "active" : "";
                container.innerHTML += `
                    <div class="contact-item ${isActive}" onclick="selectGroup('${escapeJs(grp.group_id)}', '${escapeJs(grp.group_name)}')">
                        <div class="contact-avatar" style="background: var(--accent-gradient);">👥</div>
                        <div class="contact-details">
                            <h4>${escapeHtml(grp.group_name)}</h4>
                            <p>Group (${grp.members.length} members)</p>
                        </div>
                    </div>
                `;
            });

            const allSet = new Set([...onlineUsers, ...savedContacts]);
            allSet.forEach(email => {
                if (email === currentUser || !email.toLowerCase().includes(filter.toLowerCase())) return;
                const isOnline = onlineUsers.includes(email);
                const isActive = activeContact === email ? "active" : "";

                const safeEmail = escapeHtml(email);
                const jsEmail = escapeJs(email);
                container.innerHTML += `
                    <div class="contact-item ${isActive}" onclick="selectContact('${jsEmail}')">
                        <div class="contact-avatar profile-trigger" onclick="openUserProfile('${jsEmail}', event)" title="View profile">
                            ${escapeHtml(email.charAt(0).toUpperCase())}<div class="${isOnline ? 'online-dot' : 'offline-dot'}"></div>
                        </div>
                        <div class="contact-details" onclick="openUserProfile('${jsEmail}', event)" title="View profile">
                            <h4>${safeEmail}</h4>
                            <p>${isOnline ? 'Online · View profile' : 'Offline · View profile'}</p>
                        </div>
                    </div>
                `;
            });
        }

        function filterContacts() { renderContacts(document.getElementById("searchContactInput").value); }

        async function selectContact(email) {
            activeContact = email;
            isGroupActive = false;
            document.getElementById("activeChatTitle").innerText = email;
            document.getElementById("activeChatStatus").innerText = onlineUsers.includes(email) ? "Online" : "Offline";
            document.getElementById("activeChatAvatar").innerHTML = email.charAt(0).toUpperCase();
            document.getElementById("chatHeaderActions").classList.remove("hidden");
            
            document.getElementById("messageInput").disabled = false;
            document.getElementById("app-container").classList.add("mobile-chat-open");
            
            const res = await fetch(`/history/${encodeURIComponent(currentUser)}/${encodeURIComponent(email)}`);
            const data = await res.json();
            chatHistories[email] = data.history.map(m => ({
                id: m.id,
                sender: m.sender === currentUser ? "You" : m.sender,
                type: m.type,
                content: m.content
            }));
            renderMessages();
        }

        async function selectGroup(groupId, groupName) {
            activeContact = groupId;
            isGroupActive = true;
            document.getElementById("activeChatTitle").innerText = groupName;
            document.getElementById("activeChatStatus").innerText = "Group Chat";
            document.getElementById("activeChatAvatar").innerHTML = "👥";
            document.getElementById("chatHeaderActions").classList.add("hidden");
            
            document.getElementById("messageInput").disabled = false;
            document.getElementById("app-container").classList.add("mobile-chat-open");

            const res = await fetch(`/group-history/${groupId}`);
            const data = await res.json();
            chatHistories[groupId] = data.history.map(m => ({
                id: m.id,
                sender: m.sender === currentUser ? "You" : m.sender,
                type: m.type,
                content: m.content
            }));
            renderMessages();
        }

        function returnToSidebar(e) {
            e.stopPropagation();
            document.getElementById("app-container").classList.remove("mobile-chat-open");
            activeContact = null;
        }

        async function openUserProfile(username, event) {
            if (event) event.stopPropagation();
            if (!username || username === currentUser) return;
            activeContact = username;
            isGroupActive = false;
            try {
                const res = await fetch(`/user/${encodeURIComponent(username)}`);
                const data = await res.json();
                document.getElementById("modalProfileName").innerText = username;
                document.getElementById("modalProfileStatus").innerText = data.status || (onlineUsers.includes(username) ? "Online" : "Offline");
                const avatar = document.getElementById("modalProfileAvatar");
                avatar.innerHTML = data.profile_pic ? `<img src="${escapeHtml(data.profile_pic)}" alt="Profile">` : escapeHtml(username.charAt(0).toUpperCase());
                const btnWrapper = document.getElementById("addToContactsBtnWrapper");
                if (savedContacts.includes(username)) {
                    btnWrapper.innerHTML = `<p style="color:#10b981;font-weight:600;margin-bottom:10px;">✓ Already in Contacts</p>`;
                } else {
                    btnWrapper.innerHTML = `<button class="sel-btn" style="width:100%;background:var(--accent-gradient);color:white;margin-bottom:10px;" onclick="addCurrentContactPermanent()">➕ Add to Contacts</button>`;
                }
                document.getElementById("contact-profile-modal").classList.remove("hidden");
            } catch (err) {
                console.error(err);
                alert("Could not load this profile.");
            }
        }

        async function openContactProfile() {
            if (!activeContact || isGroupActive) return;
            await openUserProfile(activeContact);
        }

        function closeContactProfile() { document.getElementById("contact-profile-modal").classList.add("hidden"); }

        async function addCurrentContactPermanent() {
            if (!activeContact || savedContacts.includes(activeContact)) return;
            await fetch("/contacts/add", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: currentUser, contact: activeContact })
            });
            savedContacts.push(activeContact);
            closeContactProfile();
            renderContacts();
            alert(`${activeContact} added permanently to your contacts!`);
        }

        function openGroupModal() {
            const listContainer = document.getElementById("groupMembersList");
            listContainer.innerHTML = "";
            const allSet = new Set([...onlineUsers, ...savedContacts]);
            allSet.forEach(email => {
                if (email === currentUser) return;
                listContainer.innerHTML += `
                    <label style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px; cursor: pointer; font-size: 13px;">
                        <input type="checkbox" class="group-member-checkbox" value="${email}"> ${email}
                    </label>
                `;
            });
            document.getElementById("group-modal").classList.remove("hidden");
        }

        function closeGroupModal() { document.getElementById("group-modal").classList.add("hidden"); }

        async function createGroupSubmit() {
            const groupName = document.getElementById("groupNameInput").value.trim();
            const checkboxes = document.querySelectorAll(".group-member-checkbox:checked");
            const members = Array.from(checkboxes).map(cb => cb.value);

            if (!groupName || members.length === 0) {
                alert("Please provide a group name and select at least one member.");
                return;
            }

            const res = await fetch("/groups/create", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ group_name: groupName, admin: currentUser, members: members })
            });
            const data = await res.json();
            userGroups.push(data);
            closeGroupModal();
            renderContacts();
            alert(`Group "${groupName}" created successfully!`);
        }

        // ==================== MULTIMEDIA & CALL FUNCTIONS ====================
        async function toggleVoiceRecording() {
            const btn = document.getElementById("voiceRecordBtn");
            if (!isRecording) {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    mediaRecorder = new MediaRecorder(stream);
                    audioChunks = [];
                    mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
                    mediaRecorder.onstop = async () => {
                        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                        const reader = new FileReader();
                        reader.onload = function() {
                            ws.send(JSON.stringify({ type: "audio_note", recipient_id: activeContact, message: reader.result, is_group: isGroupActive }));
                            if (!isGroupActive) {
                                if (!chatHistories[activeContact]) chatHistories[activeContact] = [];
                                chatHistories[activeContact].push({ id: Date.now(), sender: "You", type: "audio_note", content: reader.result });
                                renderMessages();
                            }
                        };
                        reader.readAsDataURL(audioBlob);
                    };
                    mediaRecorder.start();
                    isRecording = true;
                    btn.classList.add("recording");
                    btn.title = "Click again to stop & send voice note";
                } catch (err) {
                    alert("Microphone access denied or unavailable.");
                }
            } else {
                mediaRecorder.stop();
                isRecording = false;
                btn.classList.remove("recording");
                btn.title = "Hold/Click to record voice note";
            }
        }

        async function startCall(type) {
            if (!activeContact || isGroupActive) return;
            currentCallType = type;
            activeCallPartner = activeContact;
            
            ws.send(JSON.stringify({
                type: "call_request",
                recipient_id: activeCallPartner,
                call_type: type
            }));
            
            document.getElementById("callPartnerLabel").innerText = `Calling ${activeCallPartner}...`;
            document.getElementById("call-modal").classList.remove("hidden");
        }

        async function acceptIncomingCall() {
            document.getElementById("incoming-call-modal").classList.add("hidden");
            activeCallPartner = incomingCallData.sender_id;
            currentCallType = incomingCallData.call_type;
            
            ws.send(JSON.stringify({
                type: "call_response",
                recipient_id: activeCallPartner,
                accepted: true
            }));
            
            document.getElementById("callPartnerLabel").innerText = `Connected with ${activeCallPartner}`;
            document.getElementById("call-modal").classList.remove("hidden");
            await setupWebRTCConnection(false);
        }

        function rejectIncomingCall() {
            document.getElementById("incoming-call-modal").classList.add("hidden");
            if (incomingCallData) {
                ws.send(JSON.stringify({
                    type: "call_response",
                    recipient_id: incomingCallData.sender_id,
                    accepted: false
                }));
            }
            incomingCallData = null;
        }

        async function setupWebRTCConnection(isInitiator) {
            try {
                localStream = await navigator.mediaDevices.getUserMedia({
                    audio: true,
                    video: currentCallType === 'video'
                });
                document.getElementById("localVideo").srcObject = localStream;

                peerConnection = new RTCPeerConnection(rtcConfig);
                localStream.getTracks().forEach(track => peerConnection.addTrack(track, localStream));

                peerConnection.ontrack = event => {
                    document.getElementById("remoteVideo").srcObject = event.streams[0];
                };

                peerConnection.onicecandidate = event => {
                    if (event.candidate) {
                        ws.send(JSON.stringify({
                            type: "ice_candidate",
                            recipient_id: activeCallPartner,
                            candidate: event.candidate
                        }));
                    }
                };

                if (isInitiator) {
                    const offer = await peerConnection.createOffer();
                    await peerConnection.setLocalDescription(offer);
                    ws.send(JSON.stringify({
                        type: "offer",
                        recipient_id: activeCallPartner,
                        offer: offer
                    }));
                }
            } catch (err) {
                console.error("WebRTC Error:", err);
                alert("Could not start media stream for call.");
                closeCallModals();
            }
        }

        function endCall() {
            if (activeCallPartner) {
                ws.send(JSON.stringify({ type: "end_call", recipient_id: activeCallPartner }));
            }
            closeCallModals();
        }

        function closeCallModals() {
            document.getElementById("call-modal").classList.add("hidden");
            document.getElementById("incoming-call-modal").classList.add("hidden");
            if (localStream) {
                localStream.getTracks().forEach(track => track.stop());
                localStream = null;
            }
            if (peerConnection) {
                peerConnection.close();
                peerConnection = null;
            }
            activeCallPartner = null;
            currentCallType = null;
        }

        function escapeHTML(value) {
            return String(value ?? "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        function renderMessages() {
            const container = document.getElementById("chatMessagesContainer");
            container.innerHTML = "";
            const messages = chatHistories[activeContact] || [];
            
            messages.forEach(msg => {
                const isOutgoing = msg.sender === "You" || msg.sender === currentUser;
                let contentHTML = "";
                if (msg.type === "image") {
                    contentHTML = `<img src="${escapeHTML(msg.content)}" alt="Shared image" loading="lazy" style="max-width: 220px; border-radius: 8px;">`;
                } else if (msg.type === "audio_note") {
                    contentHTML = `<audio controls preload="metadata" src="${escapeHTML(msg.content)}" style="max-width: 220px; height: 36px;"></audio>`;
                } else {
                    contentHTML = `<span>${escapeHTML(msg.content)}</span>`;
                }
                const senderLabel = isGroupActive && !isOutgoing ? `<div style="font-size: 11px; color: var(--accent); margin-bottom: 2px; font-weight: bold;">${msg.sender}</div>` : "";

                container.innerHTML += `
                    <div class="message ${isOutgoing ? "outgoing" : "incoming"}">
                        <div class="msg-body">
                            ${senderLabel}
                            ${contentHTML}
                            ${isOutgoing ? '<span class="msg-ticks">✓✓</span>' : ''}
                        </div>
                    </div>
                `;
            });
            container.scrollTop = container.scrollHeight;
        }

        function sendMessage() {
            const input = document.getElementById("messageInput");
            const text = input.value.trim();
            if (!text || !activeContact) return;

            ws.send(JSON.stringify({
                type: "chat",
                recipient_id: activeContact,
                message: text,
                is_group: isGroupActive
            }));

            if (!isGroupActive) {
                if (!chatHistories[activeContact]) chatHistories[activeContact] = [];
                chatHistories[activeContact].push({ id: Date.now(), sender: "You", type: "chat", content: text });
                renderMessages();
            }
            input.value = "";
        }

        function handleKey(e) { if (e.key === "Enter") sendMessage(); }

        function sendImage(e) {
            const file = e.target.files[0];
            if (!file || !activeContact) return;
            const reader = new FileReader();
            reader.onload = function() {
                if (!ws || ws.readyState !== WebSocket.OPEN) { alert("Connection is offline. Please wait a moment."); return; }
                ws.send(JSON.stringify({ type: "image", recipient_id: activeContact, message: reader.result, is_group: isGroupActive }));
                if (!isGroupActive) {
                    if (!chatHistories[activeContact]) chatHistories[activeContact] = [];
                    chatHistories[activeContact].push({ id: Date.now(), sender: "You", type: "image", content: reader.result });
                    renderMessages();
                }
            };
            reader.readAsDataURL(file);
        }
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def get_index():
    return HTML_CONTENT

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
