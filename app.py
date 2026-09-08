from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import json
import sqlite3
import uuid
from typing import Dict, List, Optional
from contextlib import contextmanager

app = FastAPI(title="Metaverse WhatsApp - Advanced Multimedia Edition")

DATABASE_NAME = "metaverse_whatsapp.db"

# ==================== DATABASE SETUP & DEPENDENCY ====================
def init_db():
    with sqlite3.connect(DATABASE_NAME) as conn:
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

init_db()

@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_NAME, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

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
        for connection in self.active_connections.values():
            await connection.send_text(json.dumps(payload))

    async def send_personal_message(self, message: dict, recipient: str):
        if recipient in self.active_connections:
            await self.active_connections[recipient].send_text(json.dumps(message))

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
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (username, status, profile_pic, theme) 
            VALUES (?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET 
                status = COALESCE(?, status),
                profile_pic = COALESCE(?, profile_pic),
                theme = COALESCE(?, theme)
        """, (profile.username, profile.status, profile.profile_pic, profile.theme,
              profile.status, profile.profile_pic, profile.theme))
        conn.commit()
    return {"status": "success"}

@app.get("/user/{username}")
async def get_user(username: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, profile_pic, theme FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
    if row:
        return {"status": row[0], "profile_pic": row[1], "theme": row[2]}
    return {"status": "Hey there! I am using Metaverse WhatsApp", "profile_pic": None, "theme": "dark"}

class ContactAdd(BaseModel):
    username: str
    contact: str

@app.post("/contacts/add")
async def add_contact(data: ContactAdd):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO saved_contacts (username, contact) VALUES (?, ?)", (data.username, data.contact))
        conn.commit()
    return {"status": "success"}

@app.get("/contacts/{username}")
async def get_saved_contacts(username: str):
    with get_db() as conn:
        cursor = conn.cursor()
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
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO groups (group_id, group_name, admin, members) VALUES (?, ?, ?, ?)",
                       (group_id, group.group_name, group.admin, json.dumps(all_members)))
        conn.commit()
    return {"group_id": group_id, "group_name": group.group_name, "members": all_members}

@app.get("/groups/{username}")
async def get_user_groups(username: str):
    with get_db() as conn:
        cursor = conn.cursor()
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
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, sender, type, content FROM group_messages WHERE group_id = ? ORDER BY id ASC", (group_id,))
        rows = cursor.fetchall()
    history = [{"id": r[0], "sender": r[1], "type": r[2], "content": r[3]} for r in rows]
    return {"history": history}

@app.get("/history/{user}/{contact}")
async def get_history(user: str, contact: str):
    with get_db() as conn:
        cursor = conn.cursor()
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
            raw_data = await websocket.receive_text()
            try:
                message_data = json.loads(raw_data)
            except json.JSONDecodeError:
                continue
            
            msg_type = message_data.get("type")
            recipient_id = message_data.get("recipient_id")
            content = message_data.get("message")
            is_group = message_data.get("is_group", False)
            
            if msg_type in ["call_request", "call_response", "offer", "answer", "ice_candidate", "end_call"]:
                message_data["sender_id"] = username
                await manager.send_personal_message(message_data, recipient_id)
                continue

            with get_db() as conn:
                cursor = conn.cursor()
                if is_group:
                    cursor.execute("INSERT INTO group_messages (group_id, sender, type, content) VALUES (?, ?, ?, ?)",
                                   (recipient_id, username, msg_type, content))
                    conn.commit()
                    cursor.execute("SELECT last_insert_rowid()")
                    msg_id = cursor.fetchone()[0]

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
                else:
                    if msg_type in ["chat", "audio_note", "image"]:
                        cursor.execute("INSERT INTO messages (sender, recipient, type, content) VALUES (?, ?, ?, ?)", 
                                       (username, recipient_id, msg_type, content))
                        conn.commit()
                        cursor.execute("SELECT last_insert_rowid()")
                        msg_id = cursor.fetchone()[0]

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


# ==================== ADVANCED FRONTEND UI ====================
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Metaverse WhatsApp - Advanced Quantum Edition</title>
    <link rel="icon" href="https://img.icons8.com/color/48/whatsapp--v1.png" type="image/png">
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <style>
        :root {
            --bg-primary: #05070b;
            --bg-secondary: #0d131f;
            --bg-panel: #161e2e;
            --accent: #00f2fe;
            --accent-gradient: linear-gradient(135deg, #00f2fe, #4facfe);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #1e293b;
            --outgoing: #0d9488;
            --glass: rgba(22, 30, 46, 0.7);
        }

        body.theme-light {
            --bg-primary: #f8fafc;
            --bg-secondary: #ffffff;
            --bg-panel: #f1f5f9;
            --accent: #0284c7;
            --accent-gradient: linear-gradient(135deg, #0284c7, #0369a1);
            --text-main: #0f172a;
            --text-muted: #64748b;
            --border: #e2e8f0;
            --outgoing: #0d9488;
            --glass: rgba(255, 255, 255, 0.8);
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', system-ui, -apple-system, sans-serif; }
        body { background: var(--bg-primary); height: 100vh; display: flex; justify-content: center; align-items: center; color: var(--text-main); overflow: hidden; }
        .hidden { display: none !important; }
        
        #app-container { width: 98vw; max-width: 1600px; height: 96vh; background: var(--bg-secondary); border: 1px solid var(--border); display: flex; box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6); border-radius: 24px; overflow: hidden; position: relative; backdrop-filter: blur(20px); }
        
        #login-screen { position: absolute; inset: 0; background: var(--bg-primary); display: flex; justify-content: center; align-items: center; z-index: 200; padding: 20px; }
        #login-box { background: var(--bg-panel); border: 1px solid var(--border); padding: 45px 35px; border-radius: 24px; text-align: center; box-shadow: 0 20px 40px rgba(0,0,0,0.4); width: 100%; max-width: 440px; }
        #login-box h1 { color: var(--accent); margin-bottom: 8px; font-size: 28px; font-weight: 800; letter-spacing: -0.5px; }
        #login-box p { color: var(--text-muted); font-size: 14px; margin-bottom: 30px; }
        
        .google-btn-wrapper { display: flex; justify-content: center; margin-bottom: 20px; }
        .divider { display: flex; align-items: center; text-align: center; color: var(--text-muted); font-size: 12px; margin: 20px 0; text-transform: uppercase; letter-spacing: 1px; }
        .divider::before, .divider::after { content: ''; flex: 1; border-bottom: 1px solid var(--border); }
        .divider::before { margin-right: .75em; }
        .divider::after { margin-left: .75em; }

        #login-box input { width: 100%; padding: 14px 18px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px; color: var(--text-main); font-size: 14px; outline: none; margin-bottom: 16px; text-align: center; transition: all 0.3s ease; }
        #login-box input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(0, 242, 254, 0.15); }
        #login-box button.manual-login { width: 100%; padding: 14px; background: var(--accent-gradient); color: #fff; border: none; border-radius: 12px; font-weight: 700; font-size: 14px; cursor: pointer; transition: transform 0.2s, opacity 0.2s; }
        #login-box button.manual-login:hover { opacity: 0.9; transform: translateY(-1px); }

        .sidebar { width: 360px; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; height: 100%; }
        .sidebar-header { padding: 18px 20px; background: var(--bg-secondary); display: flex; align-items: center; justify-content: space-between; height: 80px; border-bottom: 1px solid var(--border); }
        .my-profile { font-weight: 600; color: var(--accent); font-size: 14px; display: flex; align-items: center; gap: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 170px; cursor: pointer; }
        .contact-avatar { width: 46px; height: 46px; border-radius: 50%; background: var(--accent-gradient); color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 16px; margin-right: 12px; position: relative; flex-shrink: 0; overflow: hidden; box-shadow: 0 4px 10px rgba(0,0,0,0.2); }
        .contact-avatar img { width: 100%; height: 100%; object-fit: cover; }
        .online-dot { width: 12px; height: 12px; background: #10b981; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        .offline-dot { width: 12px; height: 12px; background: #64748b; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        
        .sidebar-toolbar { padding: 14px 18px; background: var(--bg-panel); border-bottom: 1px solid var(--border); display: flex; gap: 8px; }
        .sidebar-toolbar input { flex: 1; padding: 10px 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 10px; color: var(--text-main); font-size: 13px; outline: none; transition: border-color 0.2s; }
        .sidebar-toolbar input:focus { border-color: var(--accent); }
        
        .contacts-list { flex: 1; overflow-y: auto; }
        .contact-item { display: flex; align-items: center; padding: 14px 18px; border-bottom: 1px solid var(--border); cursor: pointer; transition: background 0.2s; position: relative; }
        .contact-item:hover, .contact-item.active { background: var(--bg-secondary); }
        .contact-item.active::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: var(--accent); }
        .contact-details h4 { font-size: 14px; color: var(--text-main); font-weight: 600; }
        .contact-details p { font-size: 12px; color: var(--text-muted); margin-top: 3px; }

        .chat-panel { flex: 1; display: flex; flex-direction: column; background: var(--bg-primary); position: relative; height: 100%; }
        .chat-header { height: 80px; background: var(--bg-secondary); padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); }
        .active-chat-info { display: flex; align-items: center; gap: 14px; cursor: pointer; }
        .header-actions { display: flex; align-items: center; gap: 10px; }
        
        .header-btn { background: var(--bg-panel); color: var(--text-main); border: 1px solid var(--border); padding: 8px 14px; border-radius: 10px; cursor: pointer; font-size: 13px; font-weight: 600; transition: all 0.2s; white-space: nowrap; }
        .header-btn:hover { border-color: var(--accent); color: var(--accent); transform: translateY(-1px); }
        
        .chat-messages { flex: 1; padding: 24px; overflow-y: auto; display: flex; flex-direction: column; gap: 16px; background-image: radial-gradient(var(--border) 1px, transparent 1px); background-size: 24px 24px; }
        
        .message { max-width: 70%; padding: 14px 18px; border-radius: 16px; font-size: 14px; line-height: 22px; word-wrap: break-word; position: relative; box-shadow: 0 4px 15px rgba(0,0,0,0.1); display: flex; flex-direction: column; gap: 4px; }
        .message.incoming { background: var(--bg-panel); border: 1px solid var(--border); align-self: flex-start; border-top-left-radius: 4px; color: var(--text-main); }
        .message.outgoing { background: var(--outgoing); align-self: flex-end; border-top-right-radius: 4px; color: white; }
        
        .msg-body { flex: 1; display: flex; flex-direction: column; gap: 4px; }
        .msg-ticks { font-size: 11px; align-self: flex-end; color: rgba(255,255,255,0.8); }

        .chat-input-area { min-height: 80px; background: var(--bg-secondary); padding: 16px 24px; display: flex; align-items: center; gap: 12px; border-top: 1px solid var(--border); }
        .chat-input-area input { flex: 1; padding: 14px 18px; border: 1px solid var(--border); border-radius: 12px; background: var(--bg-panel); color: var(--text-main); font-size: 14px; outline: none; transition: border-color 0.2s; }
        .chat-input-area input:focus { border-color: var(--accent); }
        .action-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: var(--text-muted); padding: 8px; border-radius: 50%; transition: all 0.2s; display: flex; align-items: center; justify-content: center; }
        .action-btn:hover { color: var(--accent); background: var(--bg-panel); }
        .action-btn.recording { color: #ef4444; background: rgba(239, 68, 68, 0.1); animation: pulse 1.2s infinite; }

        @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.5; transform: scale(1.05); } 100% { opacity: 1; transform: scale(1); } }

        .modal-overlay { position: absolute; inset: 0; background: rgba(0,0,0,0.75); z-index: 300; display: flex; justify-content: center; align-items: center; backdrop-filter: blur(8px); padding: 20px; }
        .modal-content { background: var(--bg-panel); border: 1px solid var(--border); padding: 30px; border-radius: 20px; width: 100%; max-width: 440px; box-shadow: 0 25px 50px rgba(0,0,0,0.6); }
        .modal-content h3 { color: var(--accent); margin-bottom: 20px; font-size: 20px; font-weight: 700; }
        .modal-content label { font-size: 13px; color: var(--text-muted); display: block; margin-bottom: 6px; margin-top: 16px; font-weight: 500; }
        .modal-content input, .modal-content textarea { width: 100%; padding: 12px 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 10px; color: var(--text-main); font-size: 14px; outline: none; transition: border-color 0.2s; }
        .modal-content input:focus, .modal-content textarea:focus { border-color: var(--accent); }
        .sel-btn { background: var(--bg-secondary); border: 1px solid var(--border); color: var(--text-main); padding: 12px 20px; border-radius: 10px; cursor: pointer; font-weight: 600; font-size: 14px; transition: all 0.2s; }
        .sel-btn:hover { border-color: var(--accent); color: var(--accent); }

        /* Call Screen Modal */
        #call-modal { position: absolute; inset: 0; background: rgba(0,0,0,0.95); z-index: 400; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .call-container { width: 92%; max-width: 900px; height: 80vh; background: var(--bg-panel); border-radius: 20px; border: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; position: relative; box-shadow: 0 30px 60px rgba(0,0,0,0.8); }
        .call-videos { flex: 1; display: flex; background: #000; position: relative; justify-content: center; align-items: center; }
        #remoteVideo { width: 100%; height: 100%; object-fit: cover; }
        #localVideo { width: 160px; height: 120px; position: absolute; bottom: 20px; right: 20px; border-radius: 12px; border: 2px solid var(--accent); object-fit: cover; background: #111; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .call-controls { height: 80px; background: var(--bg-secondary); display: flex; justify-content: center; align-items: center; gap: 20px; }
        .call-btn { padding: 12px 28px; border-radius: 50px; border: none; font-weight: 700; cursor: pointer; font-size: 14px; transition: transform 0.2s; }
        .call-btn:hover { transform: scale(1.05); }
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
    </style>
</head>
<body class="theme-dark">

    <div id="app-container">
        <!-- Login Screen -->
        <div id="login-screen">
            <div id="login-box">
                <h1>⚡ Metaverse</h1>
                <p>Advanced Quantum Communications</p>
                
                <div class="google-btn-wrapper">
                    <div id="g_id_onload"
                         data-client_id="358332042325-3s7o118sjfv1qug4r6qlmf534083ti10.apps.googleusercontent.com"
                         data-callback="handleGoogleLogin"
                         data-auto_select="true">
                    </div>
                    <div class="g_id_signin" data-type="standard" data-shape="pill" data-theme="filled_black" data-size="large"></div>
                </div>

                <div class="divider">or manual access</div>
                
                <input type="text" id="loginUsernameInput" placeholder="Enter secure node handle..." onkeypress="handleLoginKey(event)">
                <button class="manual-login" onclick="performManualLogin()">Initialize Session</button>
            </div>
        </div>

        <!-- Sidebar -->
        <div class="sidebar">
            <div class="sidebar-header">
                <div class="my-profile" onclick="openSettingsModal()">
                    <div class="contact-avatar" id="myAvatarDisplay" style="width: 38px; height: 38px; font-size: 14px; margin-right: 0;">⚡</div>
                    <span id="my-profile-display">Node</span>
                </div>
                <div style="display: flex; gap: 6px;">
                    <button class="header-btn" onclick="openGroupModal()" title="New Group">👥</button>
                    <button class="header-btn" onclick="openSettingsModal()" title="Settings">⚙️</button>
                    <button class="header-btn" onclick="logout()" title="Logout">⏏</button>
                </div>
            </div>
            <div class="sidebar-toolbar">
                <input type="text" id="searchContactInput" placeholder="Search contacts & nodes..." oninput="filterContacts()">
            </div>
            <div class="contacts-list" id="contactsListContainer"></div>
        </div>

        <!-- Chat Panel -->
        <div class="chat-panel">
            <div class="chat-header">
                <div class="active-chat-info" onclick="openContactProfile()">
                    <button class="header-btn hidden" id="backToContactsBtn" onclick="returnToSidebar(event)">⬅</button>
                    <div class="contact-avatar" id="activeChatAvatar">?</div>
                    <div>
                        <h4 id="activeChatTitle" style="font-size: 15px; font-weight: 600;">Select Node</h4>
                        <p id="activeChatStatus" style="font-size: 12px; color: var(--accent);">Ready</p>
                    </div>
                </div>
                <div class="header-actions" id="chatHeaderActions">
                    <button class="header-btn" onclick="startCall('voice')" title="Voice Call">📞</button>
                    <button class="header-btn" onclick="startCall('video')" title="Video Call">📹</button>
                </div>
            </div>

            <div class="chat-messages" id="chatMessagesContainer">
                <div style="text-align: center; margin: auto; color: var(--text-muted); font-size: 14px;">
                    <p>🔒 Select a secure contact or group to begin encrypted transmission.</p>
                </div>
            </div>

            <div class="chat-input-area">
                <label class="action-btn" title="Attach Media">📎<input type="file" id="imageInput" accept="image/*" style="display:none;" onchange="sendImage(event)"></label>
                <button class="action-btn" id="voiceRecordBtn" title="Record Voice Note" onclick="toggleVoiceRecording()">🎤</button>
                <input type="text" id="messageInput" placeholder="Type a secure message..." onkeypress="handleKey(event)" disabled>
                <button class="action-btn" onclick="sendMessage()" style="color: var(--accent);" title="Send">➤</button>
            </div>
        </div>

        <!-- Settings Modal -->
        <div id="settings-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <h3>⚙️ Node Settings</h3>
                <label>Display Handle</label>
                <input type="text" id="settingsNameInput">
                
                <label>Status Bio</label>
                <textarea id="settingsStatusInput" rows="2"></textarea>
                
                <label>Profile Avatar</label>
                <input type="file" id="settingsPicInput" accept="image/*" style="padding: 8px; background: var(--bg-secondary);">

                <label>Quantum Interface Theme</label>
                <div style="display: flex; gap: 12px; margin-top: 8px; margin-bottom: 24px;">
                    <button class="sel-btn" style="flex:1;" onclick="setTheme('dark')">🌙 Dark Mode</button>
                    <button class="sel-btn" style="flex:1;" onclick="setTheme('light')">☀️ Light Mode</button>
                </div>

                <div style="display: flex; gap: 12px;">
                    <button class="sel-btn" style="flex: 1; background: var(--accent-gradient); color: white;" onclick="saveSettings()">Save Changes</button>
                    <button class="sel-btn" style="flex: 1;" onclick="closeSettingsModal()">Cancel</button>
                </div>
            </div>
        </div>

        <!-- Contact Profile Modal -->
        <div id="contact-profile-modal" class="modal-overlay hidden">
            <div class="modal-content" style="text-align: center;">
                <div class="contact-avatar" id="modalProfileAvatar" style="width: 88px; height: 88px; font-size: 36px; margin: 0 auto 16px auto;">?</div>
                <h3 id="modalProfileName" style="margin-bottom: 6px;">Node Name</h3>
                <p id="modalProfileStatus" style="color: var(--text-muted); font-size: 13px; margin-bottom: 24px;">Status...</p>
                <div id="addToContactsBtnWrapper">
                    <button class="sel-btn" style="width: 100%; background: var(--accent-gradient); color: white; margin-bottom: 12px;" onclick="addCurrentContactPermanent()">➕ Save to Contacts</button>
                </div>
                <button class="sel-btn" style="width: 100%;" onclick="closeContactProfile()">Close</button>
            </div>
        </div>

        <!-- Create Group Modal -->
        <div id="group-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <h3>👥 Create Secure Group</h3>
                <label>Group Designation</label>
                <input type="text" id="groupNameInput" placeholder="Enter group name...">
                
                <label>Select Node Members</label>
                <div id="groupMembersList" style="max-height: 200px; overflow-y: auto; margin-top: 8px; margin-bottom: 20px; border: 1px solid var(--border); border-radius: 10px; padding: 12px;"></div>

                <div style="display: flex; gap: 12px;">
                    <button class="sel-btn" style="flex: 1; background: var(--accent-gradient); color: white;" onclick="createGroupSubmit()">Deploy Group</button>
                    <button class="sel-btn" style="flex: 1;" onclick="closeGroupModal()">Cancel</button>
                </div>
            </div>
        </div>

        <!-- Call Modal -->
        <div id="call-modal" class="hidden">
            <div class="call-container">
                <div style="padding: 16px 24px; background: var(--bg-secondary); border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;">
                    <h4 id="callStatusTitle" style="color: var(--accent);">Encrypted Quantum Stream Active</h4>
                    <span id="callPartnerLabel" style="font-size: 13px; color: var(--text-muted);"></span>
                </div>
                <div class="call-videos">
                    <video id="remoteVideo" autoplay playsinline></video>
                    <video id="localVideo" autoplay playsinline muted></video>
                </div>
                <div class="call-controls">
                    <button class="call-btn end" onclick="endCall()">Terminate Stream</button>
                </div>
            </div>
        </div>

        <!-- Incoming Call Modal -->
        <div id="incoming-call-modal" class="modal-overlay hidden">
            <div class="modal-content" style="text-align: center;">
                <h3 id="incomingCallerTitle" style="margin-bottom: 12px;">Incoming Stream Request</h3>
                <p id="incomingCallTypeDesc" style="color: var(--text-muted); margin-bottom: 28px;">Establishing connection...</p>
                <div style="display: flex; gap: 14px;">
                    <button class="sel-btn" style="flex: 1; background: #10b981; color: white;" onclick="acceptIncomingCall()">Accept</button>
                    <button class="sel-btn" style="flex: 1; background: #ef4444; color: white;" onclick="rejectIncomingCall()">Decline</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let ws;
        let currentUser = localStorage.getItem("metaverse_user") || null;
        let userStatus = "Encrypted and connected.";
        let userProfilePic = "";
        let currentTheme = "dark";
        
        let onlineUsers = [];
        let savedContacts = [];
        let userGroups = [];
        let activeContact = null;
        let isGroupActive = false;
        let chatHistories = {};

        let mediaRecorder;
        let audioChunks = [];
        let isRecording = false;

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

        function handleGoogleLogin(response) {
            try {
                const base64Url = response.credential.split('.')[1];
                const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
                const jsonPayload = decodeURIComponent(atob(base64).split('').map(c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)).join(''));
                const payload = JSON.parse(jsonPayload);
                if (payload.email) initializeUserSession(payload.email);
            } catch (err) {
                alert("Google Authentication error.");
            }
        }

        function handleLoginKey(e) { if (e.key === "Enter") performManualLogin(); }

        async function performManualLogin() {
            const val = document.getElementById("loginUsernameInput").value.trim();
            if (!val) { alert("Please enter a valid handle."); return; }
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

        function connectWebSocket() {
            const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
            ws = new WebSocket(`${wsProtocol}${window.location.host}/ws/${encodeURIComponent(currentUser)}`);
            
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
                    document.getElementById("incomingCallTypeDesc").innerText = `Incoming ${data.call_type} stream`;
                    document.getElementById("incoming-call-modal").classList.remove("hidden");
                } else if (data.type === "call_response") {
                    if (data.accepted) {
                        activeCallPartner = data.sender_id;
                        document.getElementById("callPartnerLabel").innerText = `Connected with ${activeCallPartner}`;
                        await setupWebRTCConnection(true);
                    } else {
                        alert("Stream request declined.");
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

        function renderContacts(filter = "") {
            const container = document.getElementById("contactsListContainer");
            container.innerHTML = "";
            
            userGroups.forEach(grp => {
                if (!grp.group_name.toLowerCase().includes(filter.toLowerCase())) return;
                const isActive = activeContact === grp.group_id ? "active" : "";
                container.innerHTML += `
                    <div class="contact-item ${isActive}" onclick="selectGroup('${grp.group_id}', '${grp.group_name}')">
                        <div class="contact-avatar" style="background: var(--accent-gradient);">👥</div>
                        <div class="contact-details">
                            <h4>${grp.group_name}</h4>
                            <p>Group Hub (${grp.members.length} nodes)</p>
                        </div>
                    </div>
                `;
            });

            const allSet = new Set([...onlineUsers, ...savedContacts]);
            allSet.forEach(email => {
                if (email === currentUser || !email.toLowerCase().includes(filter.toLowerCase())) return;
                const isOnline = onlineUsers.includes(email);
                const isActive = activeContact === email ? "active" : "";

                container.innerHTML += `
                    <div class="contact-item ${isActive}" onclick="selectContact('${email}')">
                        <div class="contact-avatar">
                            ${email.charAt(0).toUpperCase()}<div class="${isOnline ? 'online-dot' : 'offline-dot'}"></div>
                        </div>
                        <div class="contact-details">
                            <h4>${email}</h4>
                            <p>${isOnline ? 'Active Online' : 'Offline'}</p>
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
            document.getElementById("activeChatStatus").innerText = onlineUsers.includes(email) ? "Active Online" : "Offline";
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
            document.getElementById("activeChatStatus").innerText = "Secure Group Cluster";
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

        function openContactProfile() {
            if (!activeContact || isGroupActive) return;
            document.getElementById("modalProfileName").innerText = activeContact;
            document.getElementById("modalProfileStatus").innerText = onlineUsers.includes(activeContact) ? "Active Online" : "Offline";
            document.getElementById("modalProfileAvatar").innerHTML = activeContact.charAt(0).toUpperCase();
            
            const btnWrapper = document.getElementById("addToContactsBtnWrapper");
            if (savedContacts.includes(activeContact)) {
                btnWrapper.innerHTML = `<p style="color: #10b981; font-weight: 600; margin-bottom: 12px;">✓ Saved Contact Node</p>`;
            } else {
                btnWrapper.innerHTML = `<button class="sel-btn" style="width: 100%; background: var(--accent-gradient); color: white; margin-bottom: 12px;" onclick="addCurrentContactPermanent()">➕ Save to Contacts</button>`;
            }
            document.getElementById("contact-profile-modal").classList.remove("hidden");
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
        }

        function openGroupModal() {
            const listContainer = document.getElementById("groupMembersList");
            listContainer.innerHTML = "";
            const allSet = new Set([...onlineUsers, ...savedContacts]);
            allSet.forEach(email => {
                if (email === currentUser) return;
                listContainer.innerHTML += `
                    <label style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px; cursor: pointer; font-size: 13px;">
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
                alert("Provide a group name and select at least one node member.");
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
        }

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
                } catch (err) {
                    alert("Microphone hardware access denied.");
                }
            } else {
                mediaRecorder.stop();
                isRecording = false;
                btn.classList.remove("recording");
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
            
            document.getElementById("callPartnerLabel").innerText = `Connecting stream with ${activeCallPartner}...`;
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
            
            document.getElementById("callPartnerLabel").innerText = `Secure Stream with ${activeCallPartner}`;
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
                console.error("WebRTC Protocol Error:", err);
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

        function renderMessages() {
            const container = document.getElementById("chatMessagesContainer");
            container.innerHTML = "";
            const messages = chatHistories[activeContact] || [];
            
            messages.forEach(msg => {
                const isOutgoing = msg.sender === "You" || msg.sender === currentUser;
                let contentHTML = "";
                if (msg.type === "image") {
                    contentHTML = `<img src="${msg.content}" style="max-width: 240px; border-radius: 10px; display: block;">`;
                } else if (msg.type === "audio_note") {
                    contentHTML = `<audio controls src="${msg.content}" style="max-width: 240px; height: 38px;"></audio>`;
                } else {
                    contentHTML = `<span>${msg.content}</span>`;
                }
                const senderLabel = isGroupActive && !isOutgoing ? `<div style="font-size: 11px; color: var(--accent); margin-bottom: 2px; font-weight: 700;">${msg.sender}</div>` : "";

                container.innerHTML += `
                    <div class="message ${isOutgoing ? "outgoing" : "incoming"}">
                        <div class="msg-body">
                            ${senderLabel}
                            ${contentHTML}
                        </div>
                        ${isOutgoing ? '<span class="msg-ticks">✓✓</span>' : ''}
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
