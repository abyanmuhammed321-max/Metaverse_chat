from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import json
import sqlite3
import uuid
from typing import Dict, List, Optional

app = FastAPI(title="Metaverse WhatsApp - Advanced Edition")

DB_FILE = "metaverse_whatsapp.db"

# ==================== DATABASE SETUP ====================
def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
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
        for connection in list(self.active_connections.values()):
            try:
                await connection.send_text(json.dumps(payload))
            except Exception:
                pass

    async def send_personal_message(self, message: dict, recipient: str):
        if recipient in self.active_connections:
            try:
                await self.active_connections[recipient].send_text(json.dumps(message))
            except Exception:
                pass

    async def broadcast_to_group(self, group_id: str, message: dict, members: list):
        for member in members:
            if member in self.active_connections:
                try:
                    await self.active_connections[member].send_text(json.dumps(message))
                except Exception:
                    pass

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
            return {"status": row["status"], "profile_pic": row["profile_pic"], "theme": row["theme"]}
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
        saved = [r["contact"] for r in rows]
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
            members = json.loads(r["members"])
            if username in members:
                user_groups.append({"group_id": r["group_id"], "group_name": r["group_name"], "admin": r["admin"], "members": members})
    return {"groups": user_groups}

@app.get("/group-history/{group_id}")
async def get_group_history(group_id: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, sender, type, content FROM group_messages WHERE group_id = ? ORDER BY id ASC", (group_id,))
        rows = cursor.fetchall()
        history = [{"id": r["id"], "sender": r["sender"], "type": r["type"], "content": r["content"]} for r in rows]
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
        history = [{"id": r["id"], "sender": r["sender"], "type": r["type"], "content": r["content"]} for r in rows]
    return {"history": history}

@app.delete("/history/{user}/{contact}")
async def clear_history(user: str, contact: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM messages 
            WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
        """, (user, contact, contact, user))
        conn.commit()
    return {"status": "success"}

@app.delete("/message/{msg_id}")
async def delete_message(msg_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        cursor.execute("DELETE FROM group_messages WHERE id = ?", (msg_id,))
        conn.commit()
    return {"status": "success"}

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(username, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
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
                    msg_id = cursor.lastrowid

                    cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient_id,))
                    row = cursor.fetchone()
                    if row:
                        members = json.loads(row["members"])
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
                    cursor.execute("INSERT INTO messages (sender, recipient, type, content) VALUES (?, ?, ?, ?)", 
                                   (username, recipient_id, msg_type, content))
                    conn.commit()
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

# ==================== FRONTEND UI ====================
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Metaverse WhatsApp - Quantum Edition</title>
    <link rel="icon" href="https://img.icons8.com/color/48/whatsapp--v1.png" type="image/png">
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <style>
        :root {
            --bg-primary: #0b0f19;
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
            --bg-primary: #eef2f6;
            --bg-secondary: #ffffff;
            --bg-panel: #ffffff;
            --accent: #00a884;
            --accent-gradient: linear-gradient(135deg, #00a884, #005c4b);
            --text-main: #111827;
            --text-muted: #6b7280;
            --border: #e5e7eb;
            --outgoing: #00a884;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; }
        body { background: var(--bg-primary); height: 100vh; display: flex; justify-content: center; align-items: center; color: var(--text-main); overflow: hidden; }
        .hidden { display: none !important; }
        
        /* App Container & Glassmorphism */
        #app-container { width: 98%; max-width: 1500px; height: 95vh; background: var(--bg-secondary); border: 1px solid var(--border); display: flex; box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6); border-radius: 20px; overflow: hidden; position: relative; }
        
        /* Loading Overlay Animation */
        #loading-screen { position: absolute; inset: 0; background: rgba(11, 15, 25, 0.85); backdrop-filter: blur(8px); display: flex; flex-direction: column; justify-content: center; align-items: center; z-index: 500; transition: opacity 0.3s; }
        .spinner { width: 50px; height: 50px; border: 4px solid rgba(0, 242, 254, 0.2); border-top: 4px solid var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        #loading-screen p { margin-top: 15px; font-size: 14px; letter-spacing: 1px; color: var(--accent); font-weight: 600; }

        /* Login Screen */
        #login-screen { position: absolute; inset: 0; background: var(--bg-primary); display: flex; justify-content: center; align-items: center; z-index: 200; padding: 20px; }
        #login-box { background: var(--bg-panel); border: 1px solid var(--border); padding: 50px 40px; border-radius: 24px; text-align: center; box-shadow: 0 15px 35px rgba(0,0,0,0.4); width: 100%; max-width: 420px; }
        #login-box h1 { color: var(--accent); margin-bottom: 6px; font-size: 28px; font-weight: 800; letter-spacing: -0.5px; }
        #login-box p { color: var(--text-muted); font-size: 14px; margin-bottom: 30px; }
        .google-btn-wrapper { display: flex; justify-content: center; margin-top: 10px; }

        /* Sidebar */
        .sidebar { width: 35%; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; height: 100%; }
        .sidebar-header { padding: 16px 20px; background: var(--bg-secondary); display: flex; align-items: center; justify-content: space-between; height: 75px; border-bottom: 1px solid var(--border); }
        .my-profile { font-weight: 600; color: var(--accent); font-size: 14px; display: flex; align-items: center; gap: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 170px; cursor: pointer; }
        .contact-avatar { width: 44px; height: 44px; border-radius: 50%; background: var(--accent-gradient); color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 17px; margin-right: 14px; position: relative; flex-shrink: 0; overflow: hidden; }
        .contact-avatar img { width: 100%; height: 100%; object-fit: cover; }
        .online-dot { width: 12px; height: 12px; background: #10b981; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        .offline-dot { width: 12px; height: 12px; background: #6b7280; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        
        .sidebar-toolbar { padding: 12px 18px; background: var(--bg-panel); border-bottom: 1px solid var(--border); }
        .sidebar-toolbar input { width: 100%; padding: 10px 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 10px; color: var(--text-main); font-size: 13px; outline: none; }
        .sidebar-toolbar input:focus { border-color: var(--accent); }
        
        .contacts-list { flex: 1; overflow-y: auto; }
        .contact-item { display: flex; align-items: center; padding: 14px 18px; border-bottom: 1px solid var(--border); cursor: pointer; transition: 0.2s; position: relative; user-select: none; }
        .contact-item:hover, .contact-item.active { background: var(--bg-secondary); border-left: 4px solid var(--accent); }
        .contact-details h4 { font-size: 15px; color: var(--text-main); font-weight: 500; }
        .contact-details p { font-size: 12px; color: var(--accent); margin-top: 3px; }

        /* Chat Panel */
        .chat-panel { flex: 1; display: flex; flex-direction: column; background: var(--bg-primary); position: relative; height: 100%; }
        .chat-header { height: 75px; background: var(--bg-secondary); padding: 12px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); }
        .active-chat-info { display: flex; align-items: center; gap: 12px; cursor: pointer; }
        .header-actions { display: flex; align-items: center; gap: 8px; }
        
        .header-btn { background: var(--bg-panel); color: var(--text-main); border: 1px solid var(--border); padding: 8px 14px; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 500; transition: 0.2s; white-space: nowrap; }
        .header-btn:hover { border-color: var(--accent); color: var(--accent); }
        
        .chat-messages { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
        
        /* Messages Styling */
        .message { max-width: 70%; padding: 12px 16px; border-radius: 14px; font-size: 14px; line-height: 20px; word-wrap: break-word; position: relative; box-shadow: 0 2px 5px rgba(0,0,0,0.15); display: flex; flex-direction: column; gap: 4px; }
        .message.incoming { background: var(--bg-panel); border: 1px solid var(--border); align-self: flex-start; border-top-left-radius: 2px; color: var(--text-main); }
        .message.outgoing { background: var(--outgoing); align-self: flex-end; border-top-right-radius: 2px; color: white; }
        .message.selected { outline: 2px solid var(--accent); }
        
        .msg-footer { display: flex; justify-content: space-between; align-items: center; font-size: 10px; margin-top: 4px; opacity: 0.8; }
        .msg-actions { opacity: 0; transition: 0.2s; cursor: pointer; font-size: 12px; margin-left: 8px; }
        .message:hover .msg-actions { opacity: 1; }

        .chat-input-area { min-height: 75px; background: var(--bg-secondary); padding: 12px 18px; display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--border); }
        .chat-input-area input { flex: 1; padding: 12px 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--bg-panel); color: var(--text-main); font-size: 14px; outline: none; }
        .chat-input-area input:focus { border-color: var(--accent); }
        .action-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: var(--text-muted); padding: 6px; transition: 0.2s; }
        .action-btn:hover { color: var(--accent); }
        .action-btn.recording { color: #ef4444; animation: pulse 1.2s infinite; }

        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }

        /* Custom Context Menu */
        #context-menu { position: absolute; background: var(--bg-panel); border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); width: 180px; z-index: 1000; display: none; overflow: hidden; }
        #context-menu div { padding: 10px 14px; font-size: 13px; color: var(--text-main); cursor: pointer; transition: 0.2s; }
        #context-menu div:hover { background: var(--bg-secondary); color: var(--accent); }

        /* Modals */
        .modal-overlay { position: absolute; inset: 0; background: rgba(0,0,0,0.75); z-index: 400; display: flex; justify-content: center; align-items: center; backdrop-filter: blur(4px); padding: 20px; }
        .modal-content { background: var(--bg-panel); border: 1px solid var(--border); padding: 25px; border-radius: 18px; width: 100%; max-width: 420px; box-shadow: 0 15px 35px rgba(0,0,0,0.5); }
        .modal-content h3 { color: var(--accent); margin-bottom: 16px; font-size: 18px; }
        .modal-content label { font-size: 12px; color: var(--text-muted); display: block; margin-bottom: 4px; margin-top: 12px; }
        .modal-content input, .modal-content textarea { width: 100%; padding: 10px 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 8px; color: var(--text-main); font-size: 13px; outline: none; }
        .sel-btn { background: var(--bg-secondary); border: 1px solid var(--border); color: var(--text-main); padding: 10px 16px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px; transition: 0.2s; }
        .sel-btn:hover { border-color: var(--accent); color: var(--accent); }

        /* Call Modal */
        #call-modal { position: absolute; inset: 0; background: rgba(0,0,0,0.92); z-index: 450; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .call-container { width: 90%; max-width: 800px; height: 75vh; background: var(--bg-panel); border-radius: 16px; border: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; position: relative; }
        .call-videos { flex: 1; display: flex; background: #000; position: relative; justify-content: center; align-items: center; }
        #remoteVideo { width: 100%; height: 100%; object-fit: cover; }
        #localVideo { width: 140px; height: 100px; position: absolute; bottom: 15px; right: 15px; border-radius: 8px; border: 2px solid var(--accent); object-fit: cover; background: #111; }
        .call-controls { height: 75px; background: var(--bg-secondary); display: flex; justify-content: center; align-items: center; gap: 20px; }
        .call-btn { padding: 10px 24px; border-radius: 50px; border: none; font-weight: bold; cursor: pointer; font-size: 14px; }
        .call-btn.end { background: #ef4444; color: white; }

        @media (max-width: 768px) {
            #app-container { width: 100%; height: 100vh; border-radius: 0; border: none; }
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
        <!-- Loading Animation Screen -->
        <div id="loading-screen" class="hidden">
            <div class="spinner"></div>
            <p id="loading-text">CONNECTING TO METAVERSE...</p>
        </div>

        <!-- Custom Right Click Context Menu -->
        <div id="context-menu">
            <div onclick="menuViewProfile()">👤 View Profile</div>
            <div onclick="menuAddContact()">➕ Add to Contacts</div>
            <div onclick="menuClearChat()">🗑️ Clear Chat</div>
        </div>

        <!-- Login Screen (Google Sign-In ONLY) -->
        <div id="login-screen">
            <div id="login-box">
                <h1>⚡ Metaverse</h1>
                <p>Quantum Secure Messenger</p>
                
                <div class="google-btn-wrapper">
                    <!-- Replace YOUR_GOOGLE_CLIENT_ID with actual Client ID -->
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
                    <div class="contact-avatar" id="myAvatarDisplay" style="width: 36px; height: 36px; font-size: 14px; margin-right: 0;">⚡</div>
                    <span id="my-profile-display">Google User</span>
                </div>
                <div style="display: flex; gap: 6px;">
                    <button class="header-btn" onclick="openGroupModal()" title="New Group">👥 Group</button>
                    <button class="header-btn" onclick="openSettingsModal()" title="Settings">⚙️</button>
                    <button class="header-btn" onclick="logout()" title="Logout" style="font-size: 11px;">Logout</button>
                </div>
            </div>
            <div class="sidebar-toolbar">
                <input type="text" id="searchContactInput" placeholder="Search contacts..." oninput="filterContacts()">
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
                        <p id="activeChatStatus" style="font-size: 11px; color: var(--accent);">Offline</p>
                    </div>
                </div>
                <div class="header-actions" id="chatHeaderActions">
                    <button class="header-btn" onclick="startCall('voice')" title="Voice Call">📞</button>
                    <button class="header-btn" onclick="startCall('video')" title="Video Call">📹</button>
                </div>
            </div>

            <div class="chat-messages" id="chatMessagesContainer">
                <div style="text-align: center; margin: auto; color: var(--text-muted); font-size: 13px;">
                    <p>🔒 End-to-End Encrypted Metaverse Session</p>
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
                <label>Display Status</label>
                <textarea id="settingsStatusInput" rows="2"></textarea>

                <label>Theme Mode</label>
                <div style="display: flex; gap: 10px; margin-top: 6px; margin-bottom: 20px;">
                    <button class="sel-btn" style="flex:1;" onclick="setTheme('dark')">🌙 Dark</button>
                    <button class="sel-btn" style="flex:1;" onclick="setTheme('light')">☀️ Light</button>
                </div>

                <div style="display: flex; gap: 10px;">
                    <button class="sel-btn" style="flex: 1; background: var(--accent-gradient); color: white;" onclick="saveSettings()">Save</button>
                    <button class="sel-btn" style="flex: 1;" onclick="closeSettingsModal()">Cancel</button>
                </div>
            </div>
        </div>

        <!-- Contact Profile Modal -->
        <div id="contact-profile-modal" class="modal-overlay hidden">
            <div class="modal-content" style="text-align: center;">
                <div class="contact-avatar" id="modalProfileAvatar" style="width: 80px; height: 80px; font-size: 32px; margin: 0 auto 15px auto;">?</div>
                <h3 id="modalProfileName" style="margin-bottom: 5px;">User Name</h3>
                <p id="modalProfileStatus" style="color: var(--text-muted); font-size: 13px; margin-bottom: 20px;">Status here...</p>
                <div id="addToContactsBtnWrapper">
                    <button class="sel-btn" style="width: 100%; background: var(--accent-gradient); color: white; margin-bottom: 10px;" onclick="addCurrentContactPermanent()">➕ Add to Contacts</button>
                </div>
                <button class="sel-btn" style="width: 100%; background: #ef4444; color: white; margin-bottom: 10px;" onclick="clearActiveChatHistory()">🗑️ Clear Chat</button>
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
                    <button class="sel-btn" style="flex: 1; background: var(--accent-gradient); color: white;" onclick="createGroupSubmit()">Create</button>
                    <button class="sel-btn" style="flex: 1;" onclick="closeGroupModal()">Cancel</button>
                </div>
            </div>
        </div>

        <!-- Forward Message Modal -->
        <div id="forward-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <h3>↪️ Forward Message</h3>
                <div id="forwardContactsList" style="max-height: 200px; overflow-y: auto; margin-bottom: 16px;"></div>
                <button class="sel-btn" style="width: 100%;" onclick="closeForwardModal()">Cancel</button>
            </div>
        </div>

        <!-- WebRTC Call Modal -->
        <div id="call-modal" class="hidden">
            <div class="call-container">
                <div style="padding: 15px 20px; background: var(--bg-secondary); border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;">
                    <h4 id="callStatusTitle" style="color: var(--accent);">Encrypted Call Active</h4>
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
                <p id="incomingCallTypeDesc" style="color: var(--text-muted); margin-bottom: 25px;">Call incoming...</p>
                <div style="display: flex; gap: 12px;">
                    <button class="sel-btn" style="flex: 1; background: #10b981; color: white;" onclick="acceptIncomingCall()">Accept</button>
                    <button class="sel-btn" style="flex: 1; background: #ef4444; color: white;" onclick="rejectIncomingCall()">Reject</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let ws;
        let currentUser = localStorage.getItem("metaverse_google_user") || null;
        let userPicture = localStorage.getItem("metaverse_google_pic") || "";
        let userStatus = "Hey there! I am using Metaverse WhatsApp";
        let currentTheme = "dark";
        
        let onlineUsers = [];
        let savedContacts = [];
        let userGroups = [];
        let activeContact = null;
        let contextTargetContact = null;
        let isGroupActive = false;
        let chatHistories = {};
        let selectedMessageContent = null;

        // Audio recording state
        let mediaRecorder;
        let audioChunks = [];
        let isRecording = false;

        // WebRTC Call state
        let peerConnection;
        let localStream;
        let currentCallType = null;
        let incomingCallData = null;
        let activeCallPartner = null;

        const rtcConfig = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

        window.onload = async function() {
            rpZEAWYtiB6bJ16NuLbGCc6CZ6jJdKfb63();
            if (currentUser) {
                showLoading("AUTOLOGIN IN PROGRESS...");
                await fetchUserData(currentUser);
                initializeUserSession(currentUser);
            }
            
            // Context Menu Listener
            document.addEventListener('click', () => { document.getElementById('context-menu').style.display = 'none'; });
        };

        function showLoading(msg) {
            document.getElementById("loading-text").innerText = msg;
            document.getElementById("loading-screen").classList.remove("hidden");
        }

        function hideLoading() {
            document.getElementById("loading-screen").classList.add("hidden");
        }

        function rpZEAWYtiB6bJ16NuLbGCc6CZ6jJdKfb63() {
            if ("Notification" in window && Notification.permission !== "granted") {
                Notification.requestPermission();
            }
        }

        function triggerNotification(sender, text) {
            if ("Notification" in window && Notification.permission === "granted") {
                new Notification(`New message from ${sender}`, {
                    body: text,
                    icon: 'https://img.icons8.com/color/48/whatsapp--v1.png'
                });
            }
        }

        async function fetchUserData(username) {
            try {
                const res = await fetch(`/user/${encodeURIComponent(username)}`);
                const data = await res.json();
                userStatus = data.status || userStatus;
                if (data.profile_pic) userPicture = data.profile_pic;
                currentTheme = data.theme || "dark";
                setTheme(currentTheme, false);
            } catch (err) {
                console.error("Failed to fetch user data", err);
            }
        }

        function handleGoogleLogin(response) {
            try {
                showLoading("AUTHENTICATING GOOGLE ACCOUNT...");
                const base64Url = response.credential.split('.')[1];
                const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
                const jsonPayload = decodeURIComponent(atob(base64).split('').map(c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)).join(''));
                const payload = JSON.parse(jsonPayload);
                
                if (payload.email) {
                    currentUser = payload.email;
                    userPicture = payload.picture || "";
                    localStorage.setItem("metaverse_google_user", currentUser);
                    localStorage.setItem("metaverse_google_pic", userPicture);
                    initializeUserSession(currentUser);
                }
            } catch (err) {
                hideLoading();
                alert("Google Sign-In Authentication Failed.");
            }
        }

        async function initializeUserSession(username) {
            document.getElementById("my-profile-display").innerText = username;
            if (userPicture) {
                document.getElementById("myAvatarDisplay").innerHTML = `<img src="${userPicture}">`;
            } else {
                document.getElementById("myAvatarDisplay").innerText = username.charAt(0).toUpperCase();
            }

            document.getElementById("login-screen").classList.add("hidden");
            connectWebSocket();
            await fetchSavedContacts();
            await fetchUserGroups();
            hideLoading();
        }

        function logout() {
            localStorage.removeItem("metaverse_google_user");
            localStorage.removeItem("metaverse_google_pic");
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
                body: JSON.stringify({ username: currentUser, status: userStatus, profile_pic: userPicture, theme: currentTheme })
            });
        }

        function openSettingsModal() {
            document.getElementById("settingsStatusInput").value = userStatus;
            document.getElementById("settings-modal").classList.remove("hidden");
        }

        function closeSettingsModal() { document.getElementById("settings-modal").classList.add("hidden"); }

        async function saveSettings() {
            const newStatus = document.getElementById("settingsStatusInput").value.trim();
            if (newStatus) userStatus = newStatus;
            await saveUserProfileToBackend();
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
                    if (data.sender_id !== currentUser) triggerNotification(`Group Message`, data.message);
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
                    triggerNotification(sender, data.type === 'chat' ? data.message : 'Sent a multimedia attachment');
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

                const itemHTML = document.createElement("div");
                itemHTML.className = `contact-item ${isActive}`;
                itemHTML.onclick = () => selectContact(email);
                itemHTML.oncontextmenu = (e) => showContextMenu(e, email);

                itemHTML.innerHTML = `
                    <div class="contact-avatar">
                        ${email.charAt(0).toUpperCase()}<div class="${isOnline ? 'online-dot' : 'offline-dot'}"></div>
                    </div>
                    <div class="contact-details">
                        <h4>${email}</h4>
                        <p>${isOnline ? 'Online' : 'Offline'}</p>
                    </div>
                `;
                container.appendChild(itemHTML);
            });
        }

        function showContextMenu(e, email) {
            e.preventDefault();
            contextTargetContact = email;
            const menu = document.getElementById("context-menu");
            menu.style.left = `${e.pageX}px`;
            menu.style.top = `${e.pageY}px`;
            menu.style.display = "block";
        }

        function menuViewProfile() {
            if (contextTargetContact) {
                selectContact(contextTargetContact);
                openContactProfile();
            }
        }

        async function menuAddContact() {
            if (contextTargetContact) {
                activeContact = contextTargetContact;
                await addCurrentContactPermanent();
            }
        }

        async function menuClearChat() {
            if (contextTargetContact) {
                if (confirm(`Clear chat history with ${contextTargetContact}?`)) {
                    await fetch(`/history/${encodeURIComponent(currentUser)}/${encodeURIComponent(contextTargetContact)}`, { method: "DELETE" });
                    chatHistories[contextTargetContact] = [];
                    if (activeContact === contextTargetContact) renderMessages();
                }
            }
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
            
            showLoading("LOADING CHAT HISTORY...");
            const res = await fetch(`/history/${encodeURIComponent(currentUser)}/${encodeURIComponent(email)}`);
            const data = await res.json();
            chatHistories[email] = data.history.map(m => ({
                id: m.id,
                sender: m.sender === currentUser ? "You" : m.sender,
                type: m.type,
                content: m.content
            }));
            renderMessages();
            hideLoading();
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

            showLoading("LOADING GROUP...");
            const res = await fetch(`/group-history/${groupId}`);
            const data = await res.json();
            chatHistories[groupId] = data.history.map(m => ({
                id: m.id,
                sender: m.sender === currentUser ? "You" : m.sender,
                type: m.type,
                content: m.content
            }));
            renderMessages();
            hideLoading();
        }

        function returnToSidebar(e) {
            e.stopPropagation();
            document.getElementById("app-container").classList.remove("mobile-chat-open");
            activeContact = null;
        }

        function openContactProfile() {
            if (!activeContact || isGroupActive) return;
            document.getElementById("modalProfileName").innerText = activeContact;
            document.getElementById("modalProfileStatus").innerText = onlineUsers.includes(activeContact) ? "Online" : "Offline";
            document.getElementById("modalProfileAvatar").innerHTML = activeContact.charAt(0).toUpperCase();
            
            const btnWrapper = document.getElementById("addToContactsBtnWrapper");
            if (savedContacts.includes(activeContact)) {
                btnWrapper.innerHTML = `<p style="color: #10b981; font-weight: 600; margin-bottom: 10px;">✓ In Contacts</p>`;
            } else {
                btnWrapper.innerHTML = `<button class="sel-btn" style="width: 100%; background: var(--accent-gradient); color: white; margin-bottom: 10px;" onclick="addCurrentContactPermanent()">➕ Add to Contacts</button>`;
            }
            document.getElementById("contact-profile-modal").classList.remove("hidden");
        }

        function closeContactProfile() { document.getElementById("contact-profile-modal").classList.add("hidden"); }

        async function clearActiveChatHistory() {
            if (activeContact && confirm("Are you sure you want to clear this entire chat?")) {
                await fetch(`/history/${encodeURIComponent(currentUser)}/${encodeURIComponent(activeContact)}`, { method: "DELETE" });
                chatHistories[activeContact] = [];
                renderMessages();
                closeContactProfile();
            }
        }

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
                alert("Please enter group name and pick members.");
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

        // ==================== MESSAGING & MEDIA ====================
        async function deleteMsg(msgId) {
            if (confirm("Delete this message?")) {
                await fetch(`/message/${msgId}`, { method: "DELETE" });
                if (chatHistories[activeContact]) {
                    chatHistories[activeContact] = chatHistories[activeContact].filter(m => m.id !== msgId);
                    renderMessages();
                }
            }
        }

        function forwardMsg(content) {
            selectedMessageContent = content;
            const container = document.getElementById("forwardContactsList");
            container.innerHTML = "";
            const allSet = new Set([...onlineUsers, ...savedContacts]);
            allSet.forEach(email => {
                if (email === currentUser) return;
                container.innerHTML += `
                    <button class="sel-btn" style="width: 100%; margin-bottom: 6px; text-align: left;" onclick="executeForward('${email}')">↪️ Send to ${email}</button>
                `;
            });
            document.getElementById("forward-modal").classList.remove("hidden");
        }

        function closeForwardModal() { document.getElementById("forward-modal").classList.add("hidden"); }

        function executeForward(target) {
            if (!selectedMessageContent) return;
            ws.send(JSON.stringify({
                type: "chat",
                recipient_id: target,
                message: selectedMessageContent,
                is_group: false
            }));
            closeForwardModal();
            alert(`Forwarded to ${target}`);
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
                alert("Could not initialize call hardware.");
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
            if (localStream) {
                localStream.getTracks().forEach(track => track.stop());
                localStream = null;
            }
            if (peerConnection) {
                peerConnection.close();
                peerConnection = null;
            }
            activeCallPartner = null;
        }

        function renderMessages() {
            const container = document.getElementById("chatMessagesContainer");
            container.innerHTML = "";
            const messages = chatHistories[activeContact] || [];
            
            messages.forEach(msg => {
                const isOutgoing = msg.sender === "You" || msg.sender === currentUser;
                let contentHTML = "";
                if (msg.type === "image") {
                    contentHTML = `<img src="${msg.content}" style="max-width: 220px; border-radius: 8px;">`;
                } else if (msg.type === "audio_note") {
                    contentHTML = `<audio controls src="${msg.content}" style="max-width: 220px; height: 36px;"></audio>`;
                } else {
                    contentHTML = `<span>${msg.content}</span>`;
                }
                const senderLabel = isGroupActive && !isOutgoing ? `<div style="font-size: 11px; color: var(--accent); font-weight: bold;">${msg.sender}</div>` : "";

                container.innerHTML += `
                    <div class="message ${isOutgoing ? "outgoing" : "incoming"}">
                        ${senderLabel}
                        ${contentHTML}
                        <div class="msg-footer">
                            <span>${isOutgoing ? '✓✓' : ''}</span>
                            <span class="msg-actions">
                                <span onclick="forwardMsg('${escape(msg.content)}')" title="Forward">↪️</span>
                                <span onclick="deleteMsg(${msg.id})" title="Delete">🗑️</span>
                            </span>
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
