from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import json
import sqlite3
import uuid
from typing import Dict, List, Optional

app = FastAPI(title="Metaverse WhatsApp - Google OAuth Edition")

# ==================== DATABASE SETUP ====================
def init_db():
    conn = sqlite3.connect("metaverse_whatsapp.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            display_name TEXT,
            status TEXT,
            profile_pic TEXT,
            theme TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            recipient TEXT,
            type TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_deleted INTEGER DEFAULT 0
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
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_deleted INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn

db_conn = init_db()

# ==================== WEBSOCKET CONNECTION MANAGER ====================
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, email: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[email] = websocket
        await self.broadcast_user_list()

    def disconnect(self, email: str):
        if email in self.active_connections:
            del self.active_connections[email]

    async def broadcast_user_list(self):
        user_list = list(self.active_connections.keys())
        payload = {"type": "user_list", "users": user_list}
        for connection in self.active_connections.values():
            try:
                await connection.send_text(json.dumps(payload))
            except Exception:
                pass

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
    email: str
    display_name: Optional[str] = None
    status: Optional[str] = None
    profile_pic: Optional[str] = None
    theme: Optional[str] = "dark"

@app.post("/user/update")
async def update_user(profile: UserProfile):
    cursor = db_conn.cursor()
    cursor.execute("""
        INSERT INTO users (email, display_name, status, profile_pic, theme) 
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET 
            display_name = COALESCE(?, display_name),
            status = COALESCE(?, status),
            profile_pic = COALESCE(?, profile_pic),
            theme = COALESCE(?, theme)
    """, (profile.email, profile.display_name, profile.status, profile.profile_pic, profile.theme,
          profile.display_name, profile.status, profile.profile_pic, profile.theme))
    db_conn.commit()
    return {"status": "success"}

@app.get("/user/{email}")
async def get_user(email: str):
    cursor = db_conn.cursor()
    cursor.execute("SELECT display_name, status, profile_pic, theme FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    if row:
        return {"display_name": row[0] or email.split("@")[0], "status": row[1] or "Using Metaverse Quantum", "profile_pic": row[2], "theme": row[3] or "dark"}
    return {"display_name": email.split("@")[0], "status": "Using Metaverse Quantum", "profile_pic": None, "theme": "dark"}

class ContactAdd(BaseModel):
    username: str
    contact: str

@app.post("/contacts/add")
async def add_contact(data: ContactAdd):
    cursor = db_conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO saved_contacts (username, contact) VALUES (?, ?)", (data.username, data.contact))
    db_conn.commit()
    return {"status": "success"}

@app.get("/contacts/{username}")
async def get_saved_contacts(username: str):
    cursor = db_conn.cursor()
    cursor.execute("SELECT contact FROM saved_contacts WHERE username = ?", (username,))
    rows = cursor.fetchall()
    saved = [r[0] for r in rows]
    return {"contacts": saved}

class ClearChatRequest(BaseModel):
    user: str
    contact: str
    is_group: bool = False

@app.post("/chat/clear")
async def clear_chat(req: ClearChatRequest):
    cursor = db_conn.cursor()
    if req.is_group:
        cursor.execute("DELETE FROM group_messages WHERE group_id = ?", (req.contact,))
    else:
        cursor.execute("""
            DELETE FROM messages 
            WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
        """, (req.user, req.contact, req.contact, req.user))
    db_conn.commit()
    return {"status": "cleared"}

class DeleteMessageRequest(BaseModel):
    msg_id: int
    is_group: bool = False

@app.post("/message/delete")
async def delete_message(req: DeleteMessageRequest):
    cursor = db_conn.cursor()
    if req.is_group:
        cursor.execute("UPDATE group_messages SET is_deleted = 1, content = 'This message was deleted' WHERE id = ?", (req.msg_id,))
    else:
        cursor.execute("UPDATE messages SET is_deleted = 1, content = 'This message was deleted' WHERE id = ?", (req.msg_id,))
    db_conn.commit()
    return {"status": "deleted"}

class GroupCreate(BaseModel):
    group_name: str
    admin: str
    members: List[str]

@app.post("/groups/create")
async def create_group(group: GroupCreate):
    group_id = f"group_{uuid.uuid4().hex[:8]}"
    all_members = list(set(group.members + [group.admin]))
    cursor = db_conn.cursor()
    cursor.execute("INSERT INTO groups (group_id, group_name, admin, members) VALUES (?, ?, ?, ?)",
                   (group_id, group.group_name, group.admin, json.dumps(all_members)))
    db_conn.commit()
    return {"group_id": group_id, "group_name": group.group_name, "members": all_members}

@app.get("/groups/{username}")
async def get_user_groups(username: str):
    cursor = db_conn.cursor()
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
    cursor = db_conn.cursor()
    cursor.execute("SELECT id, sender, type, content, is_deleted, timestamp FROM group_messages WHERE group_id = ? ORDER BY id ASC", (group_id,))
    rows = cursor.fetchall()
    history = [{"id": r[0], "sender": r[1], "type": r[2], "content": r[3], "is_deleted": r[4], "timestamp": r[5]} for r in rows]
    return {"history": history}

@app.get("/history/{user}/{contact}")
async def get_history(user: str, contact: str):
    cursor = db_conn.cursor()
    cursor.execute("""
        SELECT id, sender, type, content, is_deleted, timestamp FROM messages 
        WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
        ORDER BY id ASC
    """, (user, contact, contact, user))
    rows = cursor.fetchall()
    history = [{"id": r[0], "sender": r[1], "type": r[2], "content": r[3], "is_deleted": r[4], "timestamp": r[5]} for r in rows]
    return {"history": history}

@app.websocket("/ws/{email}")
async def websocket_endpoint(websocket: WebSocket, email: str):
    await manager.connect(email, websocket)
    cursor = db_conn.cursor()
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            msg_type = message_data.get("type")
            recipient_id = message_data.get("recipient_id")
            content = message_data.get("message")
            is_group = message_data.get("is_group", False)
            
            # RTC Signaling
            if msg_type in ["call_request", "call_response", "offer", "answer", "ice_candidate", "end_call"]:
                message_data["sender_id"] = email
                await manager.send_personal_message(message_data, recipient_id)
                continue

            if msg_type == "delete_msg":
                msg_id = message_data.get("msg_id")
                payload = {"type": "delete_msg", "msg_id": msg_id, "is_group": is_group, "sender_id": email}
                if is_group:
                    cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient_id,))
                    row = cursor.fetchone()
                    if row:
                        await manager.broadcast_to_group(recipient_id, payload, json.loads(row[0]))
                else:
                    await manager.send_personal_message(payload, recipient_id)
                continue

            if is_group:
                cursor.execute("INSERT INTO group_messages (group_id, sender, type, content) VALUES (?, ?, ?, ?)",
                               (recipient_id, email, msg_type, content))
                db_conn.commit()
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
                        "sender_id": email,
                        "message": content,
                        "is_group": True,
                        "is_deleted": 0
                    }
                    await manager.broadcast_to_group(recipient_id, payload, members)
                continue

            if msg_type in ["chat", "audio_note", "image"]:
                cursor.execute("INSERT INTO messages (sender, recipient, type, content) VALUES (?, ?, ?, ?)", 
                               (email, recipient_id, msg_type, content))
                db_conn.commit()
                cursor.execute("SELECT last_insert_rowid()")
                msg_id = cursor.fetchone()[0]

                payload = {
                    "id": msg_id,
                    "type": msg_type,
                    "sender_id": email,
                    "message": content,
                    "is_deleted": 0
                }
                await manager.send_personal_message(payload, recipient_id)
                
    except WebSocketDisconnect:
        manager.disconnect(email)
        await manager.broadcast_user_list()


# ==================== FRONTEND UI ====================
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Metaverse WhatsApp - Quantum Google Edition</title>
    <link rel="icon" href="https://img.icons8.com/color/48/whatsapp--v1.png" type="image/png">
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <style>
        :root {
            --bg-primary: #070a12;
            --bg-secondary: #0f172a;
            --bg-panel: #1e293b;
            --accent: #38bdf8;
            --accent-gradient: linear-gradient(135deg, #38bdf8, #6366f1);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #334155;
            --outgoing: #0d9488;
            --incoming: #1e293b;
            --danger: #ef4444;
        }

        body.theme-light {
            --bg-primary: #f1f5f9;
            --bg-secondary: #ffffff;
            --bg-panel: #f8fafc;
            --accent: #0284c7;
            --accent-gradient: linear-gradient(135deg, #0284c7, #2563eb);
            --text-main: #0f172a;
            --text-muted: #64748b;
            --border: #e2e8f0;
            --outgoing: #0284c7;
            --incoming: #e2e8f0;
            --danger: #dc2626;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', system-ui, -apple-system, sans-serif; }
        body { background: var(--bg-primary); height: 100vh; display: flex; justify-content: center; align-items: center; color: var(--text-main); overflow: hidden; }
        .hidden { display: none !important; }
        
        /* App Container */
        #app-container { width: 98%; max-width: 1550px; height: 95vh; background: var(--bg-secondary); border: 1px solid var(--border); display: flex; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5); border-radius: 20px; overflow: hidden; position: relative; }
        
        /* Loading Overlay */
        #loading-spinner-overlay { position: absolute; inset: 0; background: rgba(7, 10, 18, 0.85); backdrop-filter: blur(8px); display: flex; flex-direction: column; justify-content: center; align-items: center; z-index: 500; transition: opacity 0.3s ease; }
        .spinner { width: 48px; height: 48px; border: 4px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        
        /* Login Screen */
        #login-screen { position: absolute; inset: 0; background: var(--bg-primary); display: flex; justify-content: center; align-items: center; z-index: 200; padding: 20px; }
        #login-box { background: var(--bg-panel); border: 1px solid var(--border); padding: 50px 40px; border-radius: 24px; text-align: center; box-shadow: 0 20px 40px rgba(0,0,0,0.4); width: 100%; max-width: 440px; }
        #login-box h1 { background: var(--accent-gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 8px; font-size: 32px; font-weight: 800; }
        #login-box p { color: var(--text-muted); font-size: 14px; margin-bottom: 30px; }
        
        .google-auth-container { display: flex; flex-direction: column; align-items: center; justify-content: center; width: 100%; margin-top: 10px; }

        /* Sidebar */
        .sidebar { width: 35%; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; height: 100%; }
        .sidebar-header { padding: 16px 20px; background: var(--bg-secondary); display: flex; align-items: center; justify-content: space-between; height: 75px; border-bottom: 1px solid var(--border); }
        .my-profile { font-weight: 600; color: var(--accent); font-size: 14px; display: flex; align-items: center; gap: 12px; cursor: pointer; }
        .contact-avatar { width: 44px; height: 44px; border-radius: 50%; background: var(--accent-gradient); color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 16px; position: relative; flex-shrink: 0; overflow: hidden; border: 2px solid transparent; }
        .contact-avatar img { width: 100%; height: 100%; object-fit: cover; }
        .online-dot { width: 12px; height: 12px; background: #10b981; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        .offline-dot { width: 12px; height: 12px; background: #64748b; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        
        .sidebar-toolbar { padding: 12px 18px; background: var(--bg-panel); border-bottom: 1px solid var(--border); }
        .sidebar-toolbar input { width: 100%; padding: 10px 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 10px; color: var(--text-main); font-size: 13px; outline: none; }
        .sidebar-toolbar input:focus { border-color: var(--accent); }
        
        .contacts-list { flex: 1; overflow-y: auto; }
        .contact-item { display: flex; align-items: center; padding: 14px 18px; border-bottom: 1px solid var(--border); cursor: pointer; transition: 0.2s; position: relative; user-select: none; }
        .contact-item:hover, .contact-item.active { background: var(--bg-secondary); border-left: 4px solid var(--accent); }
        .contact-details { flex: 1; margin-left: 12px; overflow: hidden; }
        .contact-details h4 { font-size: 14px; color: var(--text-main); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .contact-details p { font-size: 12px; color: var(--text-muted); margin-top: 2px; }

        /* Chat Panel */
        .chat-panel { flex: 1; display: flex; flex-direction: column; background: var(--bg-primary); position: relative; height: 100%; }
        .chat-header { height: 75px; background: var(--bg-secondary); padding: 12px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); }
        .active-chat-info { display: flex; align-items: center; gap: 12px; cursor: pointer; }
        .header-actions { display: flex; align-items: center; gap: 8px; }
        
        .header-btn { background: var(--bg-panel); color: var(--text-main); border: 1px solid var(--border); padding: 8px 12px; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 500; transition: 0.2s; }
        .header-btn:hover { border-color: var(--accent); color: var(--accent); }
        
        .chat-messages { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
        
        .message { max-width: 70%; padding: 10px 14px; border-radius: 14px; font-size: 14px; line-height: 20px; word-wrap: break-word; position: relative; display: flex; flex-direction: column; gap: 4px; user-select: none; transition: background 0.2s; }
        .message.incoming { background: var(--incoming); align-self: flex-start; border-bottom-left-radius: 2px; color: var(--text-main); border: 1px solid var(--border); }
        .message.outgoing { background: var(--outgoing); align-self: flex-end; border-bottom-right-radius: 2px; color: #ffffff; }
        .message.selected { outline: 2px solid var(--accent); background: rgba(56, 189, 248, 0.2); }
        .message.deleted { font-style: italic; opacity: 0.7; }
        
        .msg-meta { font-size: 10px; opacity: 0.75; align-self: flex-end; margin-top: 2px; display: flex; gap: 4px; align-items: center; }

        .chat-input-area { min-height: 75px; background: var(--bg-secondary); padding: 12px 18px; display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--border); }
        .chat-input-area input { flex: 1; padding: 12px 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--bg-panel); color: var(--text-main); font-size: 14px; outline: none; }
        .chat-input-area input:focus { border-color: var(--accent); }
        .action-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: var(--text-muted); padding: 6px; transition: 0.2s; border-radius: 8px; }
        .action-btn:hover { color: var(--accent); background: var(--bg-panel); }
        .action-btn.recording { color: var(--danger); animation: pulse 1.2s infinite; }

        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }

        /* Custom Context Menu */
        #custom-context-menu { position: fixed; background: var(--bg-panel); border: 1px solid var(--border); border-radius: 12px; padding: 6px 0; z-index: 1000; box-shadow: 0 10px 25px rgba(0,0,0,0.5); min-width: 170px; }
        #custom-context-menu div { padding: 10px 16px; font-size: 13px; color: var(--text-main); cursor: pointer; transition: 0.2s; display: flex; align-items: center; gap: 8px; }
        #custom-context-menu div:hover { background: var(--accent); color: white; }

        /* Modals */
        .modal-overlay { position: absolute; inset: 0; background: rgba(0,0,0,0.75); z-index: 600; display: flex; justify-content: center; align-items: center; backdrop-filter: blur(6px); padding: 20px; }
        .modal-content { background: var(--bg-panel); border: 1px solid var(--border); padding: 30px; border-radius: 20px; width: 100%; max-width: 440px; box-shadow: 0 20px 40px rgba(0,0,0,0.5); }
        .modal-content h3 { color: var(--accent); margin-bottom: 16px; font-size: 20px; font-weight: 700; }
        .modal-content label { font-size: 12px; color: var(--text-muted); display: block; margin-bottom: 4px; margin-top: 14px; }
        .modal-content input, .modal-content textarea { width: 100%; padding: 10px 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 10px; color: var(--text-main); font-size: 13px; outline: none; }
        .sel-btn { background: var(--bg-secondary); border: 1px solid var(--border); color: var(--text-main); padding: 10px 16px; border-radius: 10px; cursor: pointer; font-weight: 600; font-size: 13px; transition: 0.2s; }
        .sel-btn:hover { border-color: var(--accent); color: var(--accent); }

        /* WebRTC Call Container */
        #call-modal { position: absolute; inset: 0; background: rgba(0,0,0,0.92); z-index: 700; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .call-container { width: 90%; max-width: 850px; height: 75vh; background: var(--bg-panel); border-radius: 20px; border: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; position: relative; }
        .call-videos { flex: 1; display: flex; background: #000; position: relative; justify-content: center; align-items: center; }
        #remoteVideo { width: 100%; height: 100%; object-fit: cover; }
        #localVideo { width: 140px; height: 100px; position: absolute; bottom: 15px; right: 15px; border-radius: 12px; border: 2px solid var(--accent); object-fit: cover; background: #111; }
        .call-controls { height: 80px; background: var(--bg-secondary); display: flex; justify-content: center; align-items: center; gap: 20px; }
        .call-btn { padding: 12px 28px; border-radius: 50px; border: none; font-weight: bold; cursor: pointer; font-size: 14px; }
        .call-btn.end { background: var(--danger); color: white; }

        @media (max-width: 768px) {
            #app-container { width: 100%; height: 100vh; border-radius: 0; border: none; }
            .sidebar { width: 100%; }
            .chat-panel { width: 100%; display: none; }
            #app-container.mobile-chat-open .sidebar { display: none; }
            #app-container.mobile-chat-open .chat-panel { display: flex; }
        }
    </style>
</head>
<body class="theme-dark">

    <div id="app-container">
        <!-- Global Loading Spinner Overlay -->
        <div id="loading-spinner-overlay">
            <div class="spinner"></div>
            <p style="margin-top: 15px; color: var(--accent); font-weight: 600; font-size: 14px;">Initializing Quantum Engine...</p>
        </div>

        <!-- Custom Right Click Context Menu -->
        <div id="custom-context-menu" class="hidden"></div>

        <!-- Google OAuth Screen -->
        <div id="login-screen">
            <div id="login-box">
                <h1>⚡ Metaverse</h1>
                <p>Quantum Encrypted Messaging</p>
                
                <div class="google-auth-container">
                    <div id="g_id_onload"
                         data-client_id="358332042325-3s7o118sjfv1qug4r6qlmf534083ti10.apps.googleusercontent.com"
                         data-callback="handleGoogleLogin"
                         data-auto_select="true">
                    </div>
                    <div class="g_id_signin" data-type="standard" data-shape="pill" data-theme="filled_black" data-size="large"></div>
                </div>
            </div>
        </div>

        <!-- Sidebar -->
        <div class="sidebar">
            <div class="sidebar-header">
                <div class="my-profile" onclick="openSettingsModal()">
                    <div class="contact-avatar" id="myAvatarDisplay">⚡</div>
                    <div>
                        <span id="my-profile-name" style="display: block; line-height: 1.2;">Google User</span>
                        <span id="my-profile-email" style="font-size: 10px; color: var(--text-muted);">email@domain.com</span>
                    </div>
                </div>
                <div style="display: flex; gap: 6px;">
                    <button class="header-btn" onclick="openGroupModal()" title="New Group">👥 Group</button>
                    <button class="header-btn" onclick="openSettingsModal()" title="Settings">⚙️</button>
                    <button class="header-btn" onclick="logout()" title="Logout" style="font-size: 11px;">🚪</button>
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
                <div class="active-chat-info" onclick="openActiveContactProfile()">
                    <button class="header-btn hidden" id="backToContactsBtn" onclick="returnToSidebar(event)">⬅️</button>
                    <div class="contact-avatar" id="activeChatAvatar">?</div>
                    <div>
                        <h4 id="activeChatTitle">Select Contact</h4>
                        <p id="activeChatStatus" style="font-size: 11px; color: var(--accent);">Quantum Encrypted</p>
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
                <button class="action-btn" id="voiceRecordBtn" title="Record Voice Note" onclick="toggleVoiceRecording()">🎤</button>
                <input type="text" id="messageInput" placeholder="Type a message..." onkeypress="handleKey(event)" disabled>
                <button class="action-btn" onclick="sendMessage()" style="color: var(--accent); font-size: 22px;" title="Send">➤</button>
            </div>
        </div>

        <!-- Settings Modal -->
        <div id="settings-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <h3>⚙️ Profile Settings</h3>
                <label>Display Name</label>
                <input type="text" id="settingsNameInput">
                
                <label>About / Custom Status</label>
                <textarea id="settingsStatusInput" rows="2"></textarea>
                
                <label>Profile Picture</label>
                <input type="file" id="settingsPicInput" accept="image/*" style="padding: 6px;">

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

        <!-- Contact / Public Profile Modal -->
        <div id="contact-profile-modal" class="modal-overlay hidden">
            <div class="modal-content" style="text-align: center;">
                <div class="contact-avatar" id="modalProfileAvatar" style="width: 90px; height: 90px; font-size: 36px; margin: 0 auto 15px auto;">?</div>
                <h3 id="modalProfileName" style="margin-bottom: 2px;">Google User</h3>
                <p id="modalProfileEmail" style="color: var(--accent); font-size: 12px; margin-bottom: 8px;">user@gmail.com</p>
                <p id="modalProfileStatus" style="color: var(--text-muted); font-size: 13px; margin-bottom: 20px;">Status...</p>
                <div id="addToContactsBtnWrapper"></div>
                <button class="sel-btn" style="width: 100%; margin-top: 8px;" onclick="closeContactProfile()">Close</button>
            </div>
        </div>

        <!-- Forward Message Modal -->
        <div id="forward-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <h3>↪️ Forward Message</h3>
                <p style="font-size: 12px; color: var(--text-muted); margin-bottom: 12px;">Select a contact or group to send this message to:</p>
                <div id="forwardTargetList" style="max-height: 200px; overflow-y: auto; border: 1px solid var(--border); border-radius: 10px; padding: 8px;"></div>
                <button class="sel-btn" style="width: 100%; margin-top: 15px;" onclick="closeForwardModal()">Cancel</button>
            </div>
        </div>

        <!-- Create Group Modal -->
        <div id="group-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <h3>👥 Create New Group</h3>
                <label>Group Name</label>
                <input type="text" id="groupNameInput" placeholder="Enter group name...">
                
                <label>Select Members</label>
                <div id="groupMembersList" style="max-height: 180px; overflow-y: auto; margin-top: 6px; margin-bottom: 16px; border: 1px solid var(--border); border-radius: 10px; padding: 10px;"></div>

                <div style="display: flex; gap: 10px;">
                    <button class="sel-btn" style="flex: 1; background: var(--accent-gradient); color: white;" onclick="createGroupSubmit()">Create</button>
                    <button class="sel-btn" style="flex: 1;" onclick="closeGroupModal()">Cancel</button>
                </div>
            </div>
        </div>

        <!-- Call WebRTC Modal -->
        <div id="call-modal" class="hidden">
            <div class="call-container">
                <div style="padding: 15px 20px; background: var(--bg-secondary); border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;">
                    <h4 id="callStatusTitle" style="color: var(--accent);">Secure Quantum Call</h4>
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
                <p id="incomingCallTypeDesc" style="color: var(--text-muted); margin-bottom: 25px;">Incoming call request...</p>
                <div style="display: flex; gap: 12px;">
                    <button class="sel-btn" style="flex: 1; background: #10b981; color: white;" onclick="acceptIncomingCall()">Accept</button>
                    <button class="sel-btn" style="flex: 1; background: var(--danger); color: white;" onclick="rejectIncomingCall()">Reject</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let ws;
        let currentUserEmail = localStorage.getItem("metaverse_google_email") || null;
        let currentDisplayName = localStorage.getItem("metaverse_google_name") || "Google User";
        let userStatus = "Using Metaverse Quantum";
        let userProfilePic = localStorage.getItem("metaverse_google_pic") || "";
        let currentTheme = "dark";
        
        let onlineUsers = [];
        let savedContacts = [];
        let userGroups = [];
        let activeContact = null;
        let isGroupActive = false;
        let chatHistories = {};
        let selectedMessageId = null;

        // Recording & WebRTC State
        let mediaRecorder, audioChunks = [], isRecording = false;
        let peerConnection, localStream, remoteStream, currentCallType = null, incomingCallData = null, activeCallPartner = null;
        const rtcConfig = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

        // Browser Notifications Permission Request
        if ("Notification" in window && Notification.permission !== "granted") {
            Notification.requestPermission();
        }

        window.onload = async function() {
            if (currentUserEmail) {
                await fetchUserData(currentUserEmail);
                initializeUserSession(currentUserEmail, currentDisplayName, userProfilePic);
            } else {
                hideLoadingSpinner();
            }
        };

        function showLoadingSpinner(msg = "Processing...") {
            const overlay = document.getElementById("loading-spinner-overlay");
            overlay.querySelector("p").innerText = msg;
            overlay.classList.remove("hidden");
        }

        function hideLoadingSpinner() {
            document.getElementById("loading-spinner-overlay").classList.add("hidden");
        }

        async function fetchUserData(email) {
            try {
                const res = await fetch(`/user/${encodeURIComponent(email)}`);
                const data = await res.json();
                currentDisplayName = data.display_name || currentDisplayName;
                userStatus = data.status || userStatus;
                userProfilePic = data.profile_pic || userProfilePic;
                currentTheme = data.theme || "dark";
                setTheme(currentTheme, false);
            } catch (err) {
                console.error("Failed to fetch user profile", err);
            }
        }

        function handleGoogleLogin(response) {
            try {
                showLoadingSpinner("Authenticating Google Account...");
                const base64Url = response.credential.split('.')[1];
                const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
                const jsonPayload = decodeURIComponent(atob(base64).split('').map(c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)).join(''));
                const payload = JSON.parse(jsonPayload);
                
                if (payload.email) {
                    currentUserEmail = payload.email;
                    currentDisplayName = payload.name || payload.email.split("@")[0];
                    userProfilePic = payload.picture || "";

                    localStorage.setItem("metaverse_google_email", currentUserEmail);
                    localStorage.setItem("metaverse_google_name", currentDisplayName);
                    localStorage.setItem("metaverse_google_pic", userProfilePic);

                    saveUserProfileToBackend().then(() => {
                        initializeUserSession(currentUserEmail, currentDisplayName, userProfilePic);
                    });
                }
            } catch (err) {
                alert("Google Authentication failed.");
                hideLoadingSpinner();
            }
        }

        async function initializeUserSession(email, name, pic) {
            showLoadingSpinner("Connecting to Metaverse Node...");
            document.getElementById("my-profile-name").innerText = name;
            document.getElementById("my-profile-email").innerText = email;
            
            if (pic) {
                document.getElementById("myAvatarDisplay").innerHTML = `<img src="${pic}">`;
            } else {
                document.getElementById("myAvatarDisplay").innerText = name.charAt(0).toUpperCase();
            }

            document.getElementById("login-screen").classList.add("hidden");
            connectWebSocket();
            await fetchSavedContacts();
            await fetchUserGroups();
            hideLoadingSpinner();
        }

        function logout() {
            localStorage.clear();
            location.reload();
        }

        function setTheme(themeName, save = true) {
            currentTheme = themeName;
            document.body.className = `theme-${themeName}`;
            if (save && currentUserEmail) saveUserProfileToBackend();
        }

        async function saveUserProfileToBackend() {
            await fetch("/user/update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email: currentUserEmail, display_name: currentDisplayName, status: userStatus, profile_pic: userProfilePic, theme: currentTheme })
            });
        }

        function openSettingsModal() {
            document.getElementById("settingsNameInput").value = currentDisplayName;
            document.getElementById("settingsStatusInput").value = userStatus;
            document.getElementById("settings-modal").classList.remove("hidden");
        }

        function closeSettingsModal() { document.getElementById("settings-modal").classList.add("hidden"); }

        async function saveSettings() {
            showLoadingSpinner("Updating profile...");
            const newName = document.getElementById("settingsNameInput").value.trim();
            const newStatus = document.getElementById("settingsStatusInput").value.trim();
            const picFile = document.getElementById("settingsPicInput").files[0];

            if (newName) currentDisplayName = newName;
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
            document.getElementById("my-profile-name").innerText = currentDisplayName;
            if (userProfilePic) document.getElementById("myAvatarDisplay").innerHTML = `<img src="${userProfilePic}">`;
            closeSettingsModal();
            hideLoadingSpinner();
        }

        async function fetchSavedContacts() {
            try {
                const res = await fetch(`/contacts/${encodeURIComponent(currentUserEmail)}`);
                const data = await res.json();
                savedContacts = data.contacts;
                renderContacts();
            } catch (err) { console.error("Failed to load contacts", err); }
        }

        async function fetchUserGroups() {
            try {
                const res = await fetch(`/groups/${encodeURIComponent(currentUserEmail)}`);
                const data = await res.json();
                userGroups = data.groups;
                renderContacts();
            } catch (err) { console.error("Failed to load groups", err); }
        }

        function connectWebSocket() {
            const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
            ws = new WebSocket(`${wsProtocol}${window.location.host}/ws/${encodeURIComponent(currentUserEmail)}`);
            
            ws.onmessage = async function(event) {
                const data = JSON.parse(event.data);
                
                if (data.type === "user_list") {
                    onlineUsers = data.users.filter(u => u !== currentUserEmail);
                    renderContacts();
                } else if (data.type === "delete_msg") {
                    const targetChat = data.is_group ? data.recipient_id : data.sender_id;
                    if (chatHistories[targetChat]) {
                        const m = chatHistories[targetChat].find(x => x.id === data.msg_id);
                        if (m) { m.is_deleted = 1; m.content = "This message was deleted"; }
                        if (activeContact === targetChat) renderMessages();
                    }
                } else if (data.is_group) {
                    const gId = data.group_id;
                    if (!chatHistories[gId]) chatHistories[gId] = [];
                    chatHistories[gId].push({ id: data.id, sender: data.sender_id, type: data.type, content: data.message, is_deleted: 0 });
                    if (activeContact === gId) renderMessages();
                    triggerNotification(`Group ${gId}`, `${data.sender_id}: ${data.message}`);
                } else if (data.type === "call_request") {
                    incomingCallData = data;
                    document.getElementById("incomingCallerTitle").innerText = `${data.sender_id} calling...`;
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
                    chatHistories[sender].push({ id: data.id, sender: sender, type: data.type, content: data.message, is_deleted: 0 });
                    if (activeContact === sender) renderMessages();
                    triggerNotification(sender, data.message);
                    renderContacts();
                }
            };
        }

        function triggerNotification(title, body) {
            if (Notification.permission === "granted" && document.hidden) {
                new Notification(title, { body: body, icon: "https://img.icons8.com/color/48/whatsapp--v1.png" });
            }
        }

        function renderContacts(filter = "") {
            const container = document.getElementById("contactsListContainer");
            container.innerHTML = "";
            
            userGroups.forEach(grp => {
                if (!grp.group_name.toLowerCase().includes(filter.toLowerCase())) return;
                const isActive = activeContact === grp.group_id ? "active" : "";
                container.innerHTML += `
                    <div class="contact-item ${isActive}" onclick="selectGroup('${grp.group_id}', '${grp.group_name}')" oncontextmenu="openContactContextMenu(event, '${grp.group_id}', true)">
                        <div class="contact-avatar">👥</div>
                        <div class="contact-details">
                            <h4>${grp.group_name}</h4>
                            <p>Group (${grp.members.length} members)</p>
                        </div>
                    </div>
                `;
            });

            const allSet = new Set([...onlineUsers, ...savedContacts]);
            allSet.forEach(email => {
                if (email === currentUserEmail || !email.toLowerCase().includes(filter.toLowerCase())) return;
                const isOnline = onlineUsers.includes(email);
                const isActive = activeContact === email ? "active" : "";

                container.innerHTML += `
                    <div class="contact-item ${isActive}" onclick="selectContact('${email}')" oncontextmenu="openContactContextMenu(event, '${email}', false)">
                        <div class="contact-avatar">
                            ${email.charAt(0).toUpperCase()}<div class="${isOnline ? 'online-dot' : 'offline-dot'}"></div>
                        </div>
                        <div class="contact-details">
                            <h4>${email}</h4>
                            <p>${isOnline ? 'Online' : 'Offline'}</p>
                        </div>
                    </div>
                `;
            });
        }

        function filterContacts() { renderContacts(document.getElementById("searchContactInput").value); }

        async function selectContact(email) {
            showLoadingSpinner("Fetching chat history...");
            activeContact = email;
            isGroupActive = false;
            document.getElementById("activeChatTitle").innerText = email;
            document.getElementById("activeChatStatus").innerText = onlineUsers.includes(email) ? "Online" : "Offline";
            document.getElementById("activeChatAvatar").innerHTML = email.charAt(0).toUpperCase();
            document.getElementById("chatHeaderActions").classList.remove("hidden");
            
            document.getElementById("messageInput").disabled = false;
            document.getElementById("app-container").classList.add("mobile-chat-open");
            
            const res = await fetch(`/history/${encodeURIComponent(currentUserEmail)}/${encodeURIComponent(email)}`);
            const data = await res.json();
            chatHistories[email] = data.history.map(m => ({
                id: m.id,
                sender: m.sender === currentUserEmail ? "You" : m.sender,
                type: m.type,
                content: m.content,
                is_deleted: m.is_deleted
            }));
            renderMessages();
            hideLoadingSpinner();
        }

        async function selectGroup(groupId, groupName) {
            showLoadingSpinner("Joining group feed...");
            activeContact = groupId;
            isGroupActive = true;
            document.getElementById("activeChatTitle").innerText = groupName;
            document.getElementById("activeChatStatus").innerText = "Group Feed";
            document.getElementById("activeChatAvatar").innerHTML = "👥";
            document.getElementById("chatHeaderActions").classList.add("hidden");
            
            document.getElementById("messageInput").disabled = false;
            document.getElementById("app-container").classList.add("mobile-chat-open");

            const res = await fetch(`/group-history/${groupId}`);
            const data = await res.json();
            chatHistories[groupId] = data.history.map(m => ({
                id: m.id,
                sender: m.sender === currentUserEmail ? "You" : m.sender,
                type: m.type,
                content: m.content,
                is_deleted: m.is_deleted
            }));
            renderMessages();
            hideLoadingSpinner();
        }

        function returnToSidebar(e) {
            e.stopPropagation();
            document.getElementById("app-container").classList.remove("mobile-chat-open");
            activeContact = null;
        }

        // ==================== CONTEXT MENUS ====================
        const contextMenu = document.getElementById("custom-context-menu");
        document.addEventListener("click", () => contextMenu.classList.add("hidden"));

        function openContactContextMenu(e, targetEmail, isGroup) {
            e.preventDefault();
            contextMenu.style.left = `${e.clientX}px`;
            contextMenu.style.top = `${e.clientY}px`;
            contextMenu.innerHTML = `
                ${!isGroup ? `<div onclick="openPublicProfile('${targetEmail}')">👤 View Google Profile</div>` : ''}
                ${!isGroup && !savedContacts.includes(targetEmail) ? `<div onclick="addContactPermanent('${targetEmail}')">➕ Add to Contacts</div>` : ''}
                <div onclick="clearChatConfirm('${targetEmail}', ${isGroup})">🗑️ Clear Chat History</div>
            `;
            contextMenu.classList.remove("hidden");
        }

        function openMessageContextMenu(e, msgId) {
            e.preventDefault();
            selectedMessageId = msgId;
            contextMenu.style.left = `${e.clientX}px`;
            contextMenu.style.top = `${e.clientY}px`;
            contextMenu.innerHTML = `
                <div onclick="forwardMessagePrompt(${msgId})">↪️ Forward Message</div>
                <div onclick="deleteMessageConfirm(${msgId})">❌ Delete Message</div>
            `;
            contextMenu.classList.remove("hidden");
        }

        async function openPublicProfile(email) {
            showLoadingSpinner("Retrieving Google Profile...");
            const res = await fetch(`/user/${encodeURIComponent(email)}`);
            const data = await res.json();
            
            document.getElementById("modalProfileName").innerText = data.display_name;
            document.getElementById("modalProfileEmail").innerText = email;
            document.getElementById("modalProfileStatus").innerText = data.status;
            
            if (data.profile_pic) {
                document.getElementById("modalProfileAvatar").innerHTML = `<img src="${data.profile_pic}">`;
            } else {
                document.getElementById("modalProfileAvatar").innerText = email.charAt(0).toUpperCase();
            }

            const btnWrapper = document.getElementById("addToContactsBtnWrapper");
            if (savedContacts.includes(email)) {
                btnWrapper.innerHTML = `<p style="color: #10b981; font-weight: 600;">✓ In Saved Contacts</p>`;
            } else {
                btnWrapper.innerHTML = `<button class="sel-btn" style="width: 100%; background: var(--accent-gradient); color: white;" onclick="addContactPermanent('${email}')">➕ Add to Contacts</button>`;
            }
            hideLoadingSpinner();
            document.getElementById("contact-profile-modal").classList.remove("hidden");
        }

        function openActiveContactProfile() {
            if (activeContact && !isGroupActive) openPublicProfile(activeContact);
        }

        function closeContactProfile() { document.getElementById("contact-profile-modal").classList.add("hidden"); }

        async function addContactPermanent(email) {
            if (savedContacts.includes(email)) return;
            await fetch("/contacts/add", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: currentUserEmail, contact: email })
            });
            savedContacts.push(email);
            closeContactProfile();
            renderContacts();
            alert(`${email} added to contacts!`);
        }

        async function clearChatConfirm(contact, isGroup) {
            if (!confirm("Are you sure you want to clear this entire chat history?")) return;
            await fetch("/chat/clear", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ user: currentUserEmail, contact: contact, is_group: isGroup })
            });
            chatHistories[contact] = [];
            if (activeContact === contact) renderMessages();
        }

        async function deleteMessageConfirm(msgId) {
            await fetch("/message/delete", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ msg_id: msgId, is_group: isGroupActive })
            });
            
            ws.send(JSON.stringify({ type: "delete_msg", msg_id: msgId, recipient_id: activeContact, is_group: isGroupActive }));
            
            if (chatHistories[activeContact]) {
                const m = chatHistories[activeContact].find(x => x.id === msgId);
                if (m) { m.is_deleted = 1; m.content = "This message was deleted"; }
            }
            renderMessages();
        }

        function forwardMessagePrompt(msgId) {
            const list = document.getElementById("forwardTargetList");
            list.innerHTML = "";
            const allSet = new Set([...onlineUsers, ...savedContacts]);
            
            allSet.forEach(email => {
                if (email === currentUserEmail) return;
                list.innerHTML += `
                    <div style="padding: 8px; border-bottom: 1px solid var(--border); cursor: pointer; display: flex; justify-content: space-between;" onclick="executeForward(${msgId}, '${email}', false)">
                        <span>👤 ${email}</span>
                        <span style="color: var(--accent);">Send ➔</span>
                    </div>
                `;
            });
            userGroups.forEach(grp => {
                list.innerHTML += `
                    <div style="padding: 8px; border-bottom: 1px solid var(--border); cursor: pointer; display: flex; justify-content: space-between;" onclick="executeForward(${msgId}, '${grp.group_id}', true)">
                        <span>👥 ${grp.group_name}</span>
                        <span style="color: var(--accent);">Send ➔</span>
                    </div>
                `;
            });
            document.getElementById("forward-modal").classList.remove("hidden");
        }

        function closeForwardModal() { document.getElementById("forward-modal").classList.add("hidden"); }

        function executeForward(msgId, target, targetIsGroup) {
            const currentMsg = (chatHistories[activeContact] || []).find(m => m.id === msgId);
            if (!currentMsg) return;

            ws.send(JSON.stringify({
                type: currentMsg.type,
                recipient_id: target,
                message: currentMsg.content,
                is_group: targetIsGroup
            }));

            closeForwardModal();
            alert("Message forwarded successfully!");
        }

        function renderMessages() {
            const container = document.getElementById("chatMessagesContainer");
            container.innerHTML = "";
            const messages = chatHistories[activeContact] || [];
            
            messages.forEach(msg => {
                const isOutgoing = msg.sender === "You" || msg.sender === currentUserEmail;
                let contentHTML = "";
                
                if (msg.is_deleted) {
                    contentHTML = `<span style="font-style: italic; opacity: 0.7;">🚫 ${msg.content}</span>`;
                } else if (msg.type === "image") {
                    contentHTML = `<img src="${msg.content}" style="max-width: 220px; border-radius: 8px;">`;
                } else if (msg.type === "audio_note") {
                    contentHTML = `<audio controls src="${msg.content}" style="max-width: 220px; height: 36px;"></audio>`;
                } else {
                    contentHTML = `<span>${msg.content}</span>`;
                }
                
                const senderLabel = isGroupActive && !isOutgoing ? `<div style="font-size: 11px; color: var(--accent); margin-bottom: 2px; font-weight: bold;">${msg.sender}</div>` : "";

                container.innerHTML += `
                    <div class="message ${isOutgoing ? "outgoing" : "incoming"} ${msg.is_deleted ? 'deleted' : ''}" oncontextmenu="openMessageContextMenu(event, ${msg.id})">
                        ${senderLabel}
                        ${contentHTML}
                        <div class="msg-meta">
                            ${isOutgoing ? '<span>✓✓</span>' : ''}
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
                chatHistories[activeContact].push({ id: Date.now(), sender: "You", type: "chat", content: text, is_deleted: 0 });
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
                    chatHistories[activeContact].push({ id: Date.now(), sender: "You", type: "image", content: reader.result, is_deleted: 0 });
                    renderMessages();
                }
            };
            reader.readAsDataURL(file);
        }

        // ==================== MULTIMEDIA & WEBRTC ====================
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
                                chatHistories[activeContact].push({ id: Date.now(), sender: "You", type: "audio_note", content: reader.result, is_deleted: 0 });
                                renderMessages();
                            }
                        };
                        reader.readAsDataURL(audioBlob);
                    };
                    mediaRecorder.start();
                    isRecording = true;
                    btn.classList.add("recording");
                } catch (err) { alert("Microphone access denied."); }
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
            
            ws.send(JSON.stringify({ type: "call_request", recipient_id: activeCallPartner, call_type: type }));
            document.getElementById("callPartnerLabel").innerText = `Calling ${activeCallPartner}...`;
            document.getElementById("call-modal").classList.remove("hidden");
        }

        async function acceptIncomingCall() {
            document.getElementById("incoming-call-modal").classList.add("hidden");
            activeCallPartner = incomingCallData.sender_id;
            currentCallType = incomingCallData.call_type;
            
            ws.send(JSON.stringify({ type: "call_response", recipient_id: activeCallPartner, accepted: true }));
            document.getElementById("callPartnerLabel").innerText = `Connected with ${activeCallPartner}`;
            document.getElementById("call-modal").classList.remove("hidden");
            await setupWebRTCConnection(false);
        }

        function rejectIncomingCall() {
            document.getElementById("incoming-call-modal").classList.add("hidden");
            if (incomingCallData) {
                ws.send(JSON.stringify({ type: "call_response", recipient_id: incomingCallData.sender_id, accepted: false }));
            }
            incomingCallData = null;
        }

        async function setupWebRTCConnection(isInitiator) {
            try {
                localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: currentCallType === 'video' });
                document.getElementById("localVideo").srcObject = localStream;

                peerConnection = new RTCPeerConnection(rtcConfig);
                localStream.getTracks().forEach(track => peerConnection.addTrack(track, localStream));

                peerConnection.ontrack = event => { document.getElementById("remoteVideo").srcObject = event.streams[0]; };
                peerConnection.onicecandidate = event => {
                    if (event.candidate) {
                        ws.send(JSON.stringify({ type: "ice_candidate", recipient_id: activeCallPartner, candidate: event.candidate }));
                    }
                };

                if (isInitiator) {
                    const offer = await peerConnection.createOffer();
                    await peerConnection.setLocalDescription(offer);
                    ws.send(JSON.stringify({ type: "offer", recipient_id: activeCallPartner, offer: offer }));
                }
            } catch (err) {
                alert("Could not initialize call media.");
                closeCallModals();
            }
        }

        function endCall() {
            if (activeCallPartner) ws.send(JSON.stringify({ type: "end_call", recipient_id: activeCallPartner }));
            closeCallModals();
        }

        function closeCallModals() {
            document.getElementById("call-modal").classList.add("hidden");
            document.getElementById("incoming-call-modal").classList.add("hidden");
            if (localStream) { localStream.getTracks().forEach(track => track.stop()); localStream = null; }
            if (peerConnection) { peerConnection.close(); peerConnection = null; }
            activeCallPartner = null;
            currentCallType = null;
        }

        function openGroupModal() {
            const listContainer = document.getElementById("groupMembersList");
            listContainer.innerHTML = "";
            const allSet = new Set([...onlineUsers, ...savedContacts]);
            allSet.forEach(email => {
                if (email === currentUserEmail) return;
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
                alert("Please select at least one contact and enter a group name.");
                return;
            }

            const res = await fetch("/groups/create", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ group_name: groupName, admin: currentUserEmail, members: members })
            });
            const data = await res.json();
            userGroups.push(data);
            closeGroupModal();
            renderContacts();
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
