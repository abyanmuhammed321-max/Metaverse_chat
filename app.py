from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import json
import sqlite3
import uuid
from typing import Dict, List, Optional

app = FastAPI(title="Metaverse_WhatsApp - Permanent Storage Edition")

# ==================== DATABASE SETUP ====================
def init_db():
    conn = sqlite3.connect("metaverse_whatsapp_advanced.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            recipient TEXT,
            type TEXT,
            content TEXT,
            is_edited INTEGER DEFAULT 0,
            is_pinned INTEGER DEFAULT 0,
            view_once INTEGER DEFAULT 0,
            reactions TEXT DEFAULT '{}',
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
            is_edited INTEGER DEFAULT 0,
            is_pinned INTEGER DEFAULT 0,
            view_once INTEGER DEFAULT 0,
            reactions TEXT DEFAULT '{}',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS polls (
            poll_id TEXT PRIMARY KEY,
            chat_id TEXT,
            question TEXT,
            options TEXT,
            votes TEXT
        )
    """)
    conn.commit()
    return conn

db_conn = init_db()
cursor = db_conn.cursor()

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
    cursor.execute("""
        INSERT INTO users (username, status, profile_pic, theme) 
        VALUES (?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET 
            status = COALESCE(?, status),
            profile_pic = COALESCE(?, profile_pic),
            theme = COALESCE(?, theme)
    """, (profile.username, profile.status, profile.profile_pic, profile.theme,
          profile.status, profile.profile_pic, profile.theme))
    db_conn.commit()
    return {"status": "success"}

@app.get("/user/{username}")
async def get_user(username: str):
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
    cursor.execute("INSERT OR IGNORE INTO saved_contacts (username, contact) VALUES (?, ?)", (data.username, data.contact))
    db_conn.commit()
    return {"status": "success"}

@app.get("/contacts/{username}")
async def get_saved_contacts(username: str):
    cursor.execute("SELECT contact FROM saved_contacts WHERE username = ?", (username,))
    rows = cursor.fetchall()
    return {"contacts": [r[0] for r in rows]}

class GroupCreate(BaseModel):
    group_name: str
    admin: str
    members: List[str]

@app.post("/groups/create")
async def create_group(group: GroupCreate):
    group_id = f"group_{uuid.uuid4().hex[:8]}"
    all_members = list(set(group.members + [group.admin]))
    cursor.execute("INSERT INTO groups (group_id, group_name, admin, members) VALUES (?, ?, ?, ?)",
                   (group_id, group.group_name, group.admin, json.dumps(all_members)))
    db_conn.commit()
    return {"group_id": group_id, "group_name": group.group_name, "members": all_members}

@app.get("/groups/{username}")
async def get_user_groups(username: str):
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
    cursor.execute("SELECT id, sender, type, content, is_edited, is_pinned, view_once, reactions FROM group_messages WHERE group_id = ? ORDER BY id ASC", (group_id,))
    rows = cursor.fetchall()
    history = [{"id": r[0], "sender": r[1], "type": r[2], "content": r[3], "is_edited": r[4], "is_pinned": r[5], "view_once": r[6], "reactions": json.loads(r[7] or '{}')} for r in rows]
    return {"history": history}

@app.get("/history/{user}/{contact}")
async def get_history(user: str, contact: str):
    cursor.execute("""
        SELECT id, sender, type, content, is_edited, is_pinned, view_once, reactions FROM messages 
        WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
        ORDER BY id ASC
    """, (user, contact, contact, user))
    rows = cursor.fetchall()
    history = [{"id": r[0], "sender": r[1], "type": r[2], "content": r[3], "is_edited": r[4], "is_pinned": r[5], "view_once": r[6], "reactions": json.loads(r[7] or '{}')} for r in rows]
    return {"history": history}

@app.get("/polls/{chat_id}")
async def get_polls(chat_id: str):
    cursor.execute("SELECT poll_id, question, options, votes FROM polls WHERE chat_id = ?", (chat_id,))
    rows = cursor.fetchall()
    polls = [{"poll_id": r[0], "question": r[1], "options": json.loads(r[2]), "votes": json.loads(r[3])} for r in rows]
    return {"polls": polls}

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(username, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            msg_type = message_data.get("type")
            recipient_id = message_data.get("recipient_id")
            content = message_data.get("message", "")
            is_group = message_data.get("is_group", False)
            
            # Typing Indicator Relay
            if msg_type == "typing":
                payload = {"type": "typing", "sender_id": username, "is_group": is_group}
                if is_group:
                    cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient_id,))
                    row = cursor.fetchone()
                    if row: await manager.broadcast_to_group(recipient_id, payload, json.loads(row[0]))
                else:
                    await manager.send_personal_message(payload, recipient_id)
                continue

            # Handle WebRTC Signaling & Calls
            if msg_type in ["call_request", "call_response", "offer", "answer", "ice_candidate", "end_call", "toggle_video", "toggle_audio"]:
                message_data["sender_id"] = username
                await manager.send_personal_message(message_data, recipient_id)
                continue

            # Handle Message Editing
            if msg_type == "edit_message":
                msg_id = message_data.get("msg_id")
                new_content = message_data.get("new_content")
                table = "group_messages" if is_group else "messages"
                cursor.execute(f"UPDATE {table} SET content = ?, is_edited = 1 WHERE id = ?", (new_content, msg_id))
                db_conn.commit()
                payload = {"type": "edit_message", "id": msg_id, "new_content": new_content, "is_group": is_group, "recipient_id": recipient_id}
                if is_group:
                    cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient_id,))
                    row = cursor.fetchone()
                    if row: await manager.broadcast_to_group(recipient_id, payload, json.loads(row[0]))
                else:
                    await manager.send_personal_message(payload, recipient_id)
                    await manager.send_personal_message(payload, username)
                continue

            # Handle Reactions
            if msg_type == "reaction":
                msg_id = message_data.get("msg_id")
                emoji = message_data.get("emoji")
                table = "group_messages" if is_group else "messages"
                cursor.execute(f"SELECT reactions FROM {table} WHERE id = ?", (msg_id,))
                row = cursor.fetchone()
                if row:
                    reactions = json.loads(row[0] or '{}')
                    reactions[username] = emoji
                    cursor.execute(f"UPDATE {table} SET reactions = ? WHERE id = ?", (json.dumps(reactions), msg_id))
                    db_conn.commit()
                    payload = {"type": "reaction", "id": msg_id, "reactions": reactions, "is_group": is_group}
                    if is_group:
                        cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient_id,))
                        row_g = cursor.fetchone()
                        if row_g: await manager.broadcast_to_group(recipient_id, payload, json.loads(row_g[0]))
                    else:
                        await manager.send_personal_message(payload, recipient_id)
                        await manager.send_personal_message(payload, username)
                continue

            # Handle Message Pinning
            if msg_type == "pin_message":
                msg_id = message_data.get("msg_id")
                pin_state = message_data.get("pin_state", 1)
                table = "group_messages" if is_group else "messages"
                cursor.execute(f"UPDATE {table} SET is_pinned = ? WHERE id = ?", (pin_state, msg_id))
                db_conn.commit()
                payload = {"type": "pin_message", "id": msg_id, "is_pinned": pin_state, "is_group": is_group}
                if is_group:
                    cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient_id,))
                    row_g = cursor.fetchone()
                    if row_g: await manager.broadcast_to_group(recipient_id, payload, json.loads(row_g[0]))
                else:
                    await manager.send_personal_message(payload, recipient_id)
                    await manager.send_personal_message(payload, username)
                continue

            # Handle Polls
            if msg_type == "poll_create":
                poll_id = f"poll_{uuid.uuid4().hex[:8]}"
                question = message_data.get("question")
                options = message_data.get("options", [])
                votes = {opt: [] for opt in options}
                cursor.execute("INSERT INTO polls (poll_id, chat_id, question, options, votes) VALUES (?, ?, ?, ?, ?)",
                               (poll_id, recipient_id, question, json.dumps(options), json.dumps(votes)))
                db_conn.commit()
                payload = {"type": "poll_create", "poll_id": poll_id, "question": question, "options": options, "votes": votes, "is_group": is_group}
                if is_group:
                    cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient_id,))
                    row_g = cursor.fetchone()
                    if row_g: await manager.broadcast_to_group(recipient_id, payload, json.loads(row_g[0]))
                else:
                    await manager.send_personal_message(payload, recipient_id)
                    await manager.send_personal_message(payload, username)
                continue

            if msg_type == "poll_vote":
                poll_id = message_data.get("poll_id")
                selected_opt = message_data.get("option")
                cursor.execute("SELECT options, votes FROM polls WHERE poll_id = ?", (poll_id,))
                row = cursor.fetchone()
                if row:
                    options = json.loads(row[0])
                    votes = json.loads(row[1])
                    for opt in votes:
                        if username in votes[opt]:
                            votes[opt].remove(username)
                    if selected_opt in votes:
                        votes[selected_opt].append(username)
                    cursor.execute("UPDATE polls SET votes = ? WHERE poll_id = ?", (json.dumps(votes), poll_id))
                    db_conn.commit()
                    payload = {"type": "poll_update", "poll_id": poll_id, "votes": votes, "is_group": is_group}
                    if is_group:
                        cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient_id,))
                        row_g = cursor.fetchone()
                        if row_g: await manager.broadcast_to_group(recipient_id, payload, json.loads(row_g[0]))
                    else:
                        await manager.send_personal_message(payload, recipient_id)
                        await manager.send_personal_message(payload, username)
                continue

            # Standard Chat, Images, Audio Notes with View Once support
            view_once = 1 if message_data.get("view_once", False) else 0
            if is_group:
                cursor.execute("INSERT INTO group_messages (group_id, sender, type, content, view_once) VALUES (?, ?, ?, ?, ?)",
                               (recipient_id, username, msg_type, content, view_once))
                db_conn.commit()
                cursor.execute("SELECT last_insert_rowid()")
                msg_id = cursor.fetchone()[0]

                cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient_id,))
                row = cursor.fetchone()
                if row:
                    payload = {
                        "id": msg_id, "group_id": recipient_id, "type": msg_type,
                        "sender_id": username, "content": content, "is_group": True,
                        "view_once": view_once, "is_edited": 0, "is_pinned": 0, "reactions": {}
                    }
                    await manager.broadcast_to_group(recipient_id, payload, json.loads(row[0]))
                continue

            if msg_type in ["chat", "audio_note", "image"]:
                cursor.execute("INSERT INTO messages (sender, recipient, type, content, view_once) VALUES (?, ?, ?, ?, ?)", 
                               (username, recipient_id, msg_type, content, view_once))
                db_conn.commit()
                cursor.execute("SELECT last_insert_rowid()")
                msg_id = cursor.fetchone()[0]

                payload = {
                    "id": msg_id, "type": msg_type, "sender_id": username, "content": content,
                    "view_once": view_once, "is_edited": 0, "is_pinned": 0, "reactions": {}
                }
                await manager.send_personal_message(payload, recipient_id)
                await manager.send_personal_message(payload, username)
                
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
    <title>Metaverse WhatsApp - Permanent Storage & Google Sign-In</title>
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

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }
        body { background: var(--bg-primary); height: 100vh; display: flex; justify-content: center; align-items: center; color: var(--text-main); overflow: hidden; }
        .hidden { display: none !important; }
        
        #app-container { width: 98%; max-width: 1550px; height: 96vh; background: var(--bg-secondary); border: 1px solid var(--border); display: flex; box-shadow: 0 0 40px rgba(0, 0, 0, 0.5); border-radius: 18px; overflow: hidden; position: relative; }
        
        #login-screen { position: absolute; inset: 0; background: var(--bg-primary); display: flex; justify-content: center; align-items: center; z-index: 200; padding: 15px; }
        #login-box { background: var(--bg-panel); border: 1px solid var(--border); padding: 40px 30px; border-radius: 20px; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.3); width: 100%; max-width: 440px; }
        #login-box h1 { color: var(--accent); margin-bottom: 8px; font-size: 26px; font-weight: 700; }
        #login-box p { color: var(--text-muted); font-size: 13px; margin-bottom: 25px; }
        
        #login-box input { width: 100%; padding: 12px 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 10px; color: var(--text-main); font-size: 14px; outline: none; margin-bottom: 14px; text-align: center; }
        #login-box input:focus { border-color: var(--accent); }
        #login-box button.manual-login { width: 100%; padding: 12px; background: var(--accent-gradient); color: #fff; border: none; border-radius: 10px; font-weight: bold; font-size: 14px; cursor: pointer; transition: 0.2s; margin-bottom: 15px; }

        .divider { display: flex; align-items: center; text-align: center; color: var(--text-muted); font-size: 12px; margin: 15px 0; }
        .divider::before, .divider::after { content: ''; flex: 1; border-bottom: 1px solid var(--border); }
        .divider::before { margin-right: .75em; }
        .divider::after { margin-left: .75em; }

        .sidebar { width: 35%; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; height: 100%; }
        .sidebar-header { padding: 16px 20px; background: var(--bg-secondary); display: flex; align-items: center; justify-content: space-between; height: 75px; border-bottom: 1px solid var(--border); }
        .my-profile { font-weight: 600; color: var(--accent); font-size: 14px; display: flex; align-items: center; gap: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 170px; cursor: pointer; }
        .contact-avatar { width: 48px; height: 48px; border-radius: 50%; background: var(--accent-gradient); color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 18px; margin-right: 14px; position: relative; flex-shrink: 0; overflow: hidden; }
        .contact-avatar img { width: 100%; height: 100%; object-fit: cover; }
        .online-dot { width: 12px; height: 12px; background: #10b981; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        .offline-dot { width: 12px; height: 12px; background: #6b7280; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        
        .sidebar-toolbar { padding: 12px 18px; background: var(--bg-panel); border-bottom: 1px solid var(--border); display: flex; gap: 8px; }
        .sidebar-toolbar input { flex: 1; padding: 10px 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 8px; color: var(--text-main); font-size: 13px; outline: none; }
        
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
        
        #pinned-banner { background: var(--bg-panel); border-bottom: 1px solid var(--border); padding: 8px 16px; font-size: 12px; display: flex; align-items: center; justify-content: space-between; color: var(--accent); }
        #typing-indicator-bar { background: var(--bg-secondary); padding: 4px 20px; font-size: 11px; color: var(--accent); font-style: italic; }

        .chat-messages { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; }
        
        .message { max-width: 75%; padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 22px; word-wrap: break-word; position: relative; box-shadow: 0 2px 5px rgba(0,0,0,0.1); display: flex; flex-direction: column; gap: 6px; }
        .message.incoming { background: var(--bg-panel); border: 1px solid var(--border); align-self: flex-start; border-top-left-radius: 2px; color: var(--text-main); }
        .message.outgoing { background: var(--outgoing); align-self: flex-end; border-top-right-radius: 2px; color: white; }
        
        .msg-actions-row { display: flex; gap: 6px; align-items: center; margin-top: 4px; font-size: 11px; }
        .msg-action-btn { background: rgba(0,0,0,0.2); border: none; color: inherit; padding: 2px 6px; border-radius: 4px; cursor: pointer; font-size: 10px; }
        .reactions-badge { display: inline-flex; gap: 4px; background: rgba(0,0,0,0.25); padding: 2px 6px; border-radius: 10px; font-size: 12px; margin-top: 4px; width: fit-content; }

        .chat-input-area { min-height: 75px; background: var(--bg-secondary); padding: 12px 18px; display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--border); }
        .chat-input-area input { flex: 1; padding: 12px 14px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg-panel); color: var(--text-main); font-size: 14px; outline: none; }
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

        #call-modal { position: absolute; inset: 0; background: rgba(0,0,0,0.92); z-index: 400; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .call-container { width: 90%; max-width: 850px; height: 80vh; background: var(--bg-panel); border-radius: 16px; border: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; position: relative; }
        .call-videos { flex: 1; display: flex; background: #000; position: relative; justify-content: center; align-items: center; }
        #remoteVideo { width: 100%; height: 100%; object-fit: cover; }
        .call-controls { height: 75px; background: var(--bg-secondary); display: flex; justify-content: center; align-items: center; gap: 16px; }
        .call-btn { padding: 10px 22px; border-radius: 50px; border: none; font-weight: bold; cursor: pointer; font-size: 14px; }
        .call-btn.end { background: #ef4444; color: white; }
        .call-btn.control { background: var(--bg-panel); border: 1px solid var(--border); color: var(--text-main); }

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
                <p>Permanent Storage & Google Sign-In</p>
                
                <div id="g_id_onload"
                     data-client_id="358332042325-3s7o118sjfv1qug4r6qlmf534083ti10.apps.googleusercontent.com"
                     data-callback="handleGoogleLogin"
                     data-auto_prompt="false">
                </div>
                <div class="g_id_signin" data-type="standard" data-shape="rectangular" data-theme="outline" data-text="sign_in_with" data-size="large" data-logo_alignment="left" style="display: flex; justify-content: center; margin-bottom: 10px;"></div>

                <div class="divider">or manual login</div>

                <input type="text" id="loginUsernameInput" placeholder="Enter username (e.g. alex@meta.com)" onkeypress="handleLoginKey(event)">
                <button class="manual-login" onclick="performManualLogin()">Launch Secure Session</button>
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
                        <p id="activeChatStatus" style="font-size: 11px; color: var(--accent);">Ready</p>
                    </div>
                </div>
                <div class="header-actions" id="chatHeaderActions">
                    <input type="text" id="messageSearchInput" placeholder="Find in chat..." oninput="filterChatMessages()" style="padding: 6px 10px; background: var(--bg-panel); border: 1px solid var(--border); border-radius: 6px; color: var(--text-main); font-size: 12px; width: 120px; outline: none;">
                    <button class="header-btn" onclick="openPollModal()" title="Create Poll">📊 Poll</button>
                    <button class="header-btn" onclick="startCall('voice')" title="Voice Call">📞</button>
                    <button class="header-btn" onclick="startCall('video')" title="Video Call">📹</button>
                </div>
            </div>

            <div id="pinned-banner" class="hidden">
                <span>📌 Pinned Message: <strong id="pinnedBannerText">None</strong></span>
            </div>
            <div id="typing-indicator-bar" class="hidden">...typing</div>

            <div class="chat-messages" id="chatMessagesContainer">
                <div style="text-align: center; margin: auto; color: var(--text-muted); font-size: 13px;">
                    <p>🔒 Select a contact or group to begin encrypted communication.</p>
                </div>
            </div>

            <div class="chat-input-area">
                <label class="action-btn" title="Attach Image">📎<input type="file" id="imageInput" accept="image/*" style="display:none;" onchange="sendImage(event)"></label>
                <button class="action-btn" id="voiceRecordBtn" title="Record voice note" onclick="toggleVoiceRecording()">🎤</button>
                <label style="font-size: 11px; color: var(--text-muted); display: flex; align-items: center; gap: 3px; cursor: pointer;" title="View Once Mode">
                    <input type="checkbox" id="viewOnceCheckbox"> 1️⃣ View Once
                </label>
                <input type="text" id="messageInput" placeholder="Type a message..." oninput="sendTypingSignal()" onkeypress="handleKey(event)" disabled>
                <button class="action-btn" onclick="sendMessage()" style="color: var(--accent); font-size: 22px;" title="Send">➤</button>
            </div>
        </div>

        <!-- Poll Modal -->
        <div id="poll-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <h3>📊 Create Interactive Poll</h3>
                <label>Poll Question</label>
                <input type="text" id="pollQuestionInput" placeholder="Ask a question...">
                <label>Options (comma separated)</label>
                <input type="text" id="pollOptionsInput" placeholder="Yes, No, Maybe">
                <div style="display: flex; gap: 10px; margin-top: 20px;">
                    <button class="sel-btn" style="flex: 1; background: var(--accent-gradient); color: white;" onclick="submitPoll()">Publish Poll</button>
                    <button class="sel-btn" style="flex: 1;" onclick="closePollModal()">Cancel</button>
                </div>
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
                <p id="modalProfileStatus" style="color: var(--text-muted); font-size: 13px; margin-bottom: 20px;">Status...</p>
                <div id="addToContactsBtnWrapper"></div>
                <button class="sel-btn" style="width: 100%; margin-top: 10px;" onclick="closeContactProfile()">Close</button>
            </div>
        </div>

        <!-- Create Group Modal -->
        <div id="group-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <h3>👥 Create New Group</h3>
                <label>Group Name</label>
                <input type="text" id="groupNameInput" placeholder="Group name...">
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
                    <video id="video-local" autoplay playsinline muted style="width: 130px; height: 95px; position: absolute; bottom: 15px; right: 15px; border-radius: 8px; border: 2px solid var(--accent); object-fit: cover; background: #222;"></video>
                </div>
                <div class="call-controls">
                    <button class="call-btn control" onclick="toggleAudioMute()" id="muteAudioBtn">🎤 Mute</button>
                    <button class="call-btn control" onclick="toggleVideoFeed()" id="toggleVideoBtn">📹 Stop Video</button>
                    <button class="call-btn control" onclick="startScreenShare()" id="screenShareBtn">🖥️ Share Screen</button>
                    <button class="call-btn end" onclick="endCall()">End Call</button>
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
        let activePolls = {};
        let typingTimeout = null;

        let mediaRecorder;
        let audioChunks = [];
        let isRecording = false;

        let peerConnection;
        let localStream;
        let currentCallType = null;
        let activeCallPartner = null;
        const rtcConfig = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

        window.onload = async function() {
            if (currentUser) {
                await fetchUserData(currentUser);
                initializeUserSession(currentUser);
            }
        };

        function decodeJwtResponse(token) {
            let base64Url = token.split('.')[1];
            let base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
            let jsonPayload = decodeURIComponent(window.atob(base64).split('').map(c => '%' + ('0' + c.charCodeAt(0).toString(16)).slice(-2)).join(''));
            return JSON.parse(jsonPayload);
        }

        async function handleGoogleLogin(response) {
            const responsePayload = decodeJwtResponse(response.credential);
            const email = responsePayload.email;
            const picture = responsePayload.picture;
            
            userProfilePic = picture || "";
            await fetchUserData(email);
            if (!userProfilePic && picture) userProfilePic = picture;
            await initializeUserSession(email);
        }

        async function fetchUserData(username) {
            try {
                const res = await fetch(`/user/${encodeURIComponent(username)}`);
                const data = await res.json();
                userStatus = data.status || userStatus;
                userProfilePic = data.profile_pic || userProfilePic;
                currentTheme = data.theme || "dark";
                setTheme(currentTheme, false);
            } catch (err) { console.error(err); }
        }

        function handleLoginKey(e) { if (e.key === "Enter") performManualLogin(); }

        async function performManualLogin() {
            const val = document.getElementById("loginUsernameInput").value.trim();
            if (!val) { alert("Please enter a username or use Google Sign-In."); return; }
            await fetchUserData(val);
            initializeUserSession(val);
        }

        async function initializeUserSession(username) {
            currentUser = username;
            localStorage.setItem("metaverse_user", currentUser);
            document.getElementById("my-profile-display").innerText = currentUser;
            if (userProfilePic) document.getElementById("myAvatarDisplay").innerHTML = `<img src="${userProfilePic}">`;
            document.getElementById("login-screen").classList.add("hidden");
            await saveUserProfileToBackend();
            connectWebSocket();
            await fetchSavedContacts();
            await fetchUserGroups();
        }

        function logout() { localStorage.removeItem("metaverse_user"); location.reload(); }

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
            if (newName && newName !== currentUser) { currentUser = newName; localStorage.setItem("metaverse_user", currentUser); }
            if (newStatus) userStatus = newStatus;
            if (picFile) {
                const reader = new FileReader();
                reader.onload = async function() { userProfilePic = reader.result; await finishSavingSettings(); };
                reader.readAsDataURL(picFile);
            } else { await finishSavingSettings(); }
        }

        async function finishSavingSettings() {
            await saveUserProfileToBackend();
            document.getElementById("my-profile-display").innerText = currentUser;
            if (userProfilePic) document.getElementById("myAvatarDisplay").innerHTML = `<img src="${userProfilePic}">`;
            closeSettingsModal();
        }

        async function fetchSavedContacts() {
            const res = await fetch(`/contacts/${encodeURIComponent(currentUser)}`);
            const data = await res.json();
            savedContacts = data.contacts;
            renderContacts();
        }

        async function fetchUserGroups() {
            const res = await fetch(`/groups/${encodeURIComponent(currentUser)}`);
            const data = await res.json();
            userGroups = data.groups;
            renderContacts();
        }

        async function fetchPollsForChat(chatId) {
            const res = await fetch(`/polls/${chatId}`);
            const data = await res.json();
            activePolls = {};
            data.polls.forEach(p => activePolls[p.poll_id] = p);
        }

        function connectWebSocket() {
            const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
            ws = new WebSocket(`${wsProtocol}${window.location.host}/ws/${encodeURIComponent(currentUser)}`);
            
            ws.onmessage = async function(event) {
                const data = JSON.parse(event.data);
                
                if (data.type === "user_list") {
                    onlineUsers = data.users.filter(u => u !== currentUser);
                    renderContacts();
                } else if (data.type === "typing") {
                    if (data.sender_id === activeContact || (data.is_group && isGroupActive)) {
                        const bar = document.getElementById("typing-indicator-bar");
                        bar.innerText = `${data.sender_id} is typing...`;
                        bar.classList.remove("hidden");
                        clearTimeout(typingTimeout);
                        typingTimeout = setTimeout(() => bar.classList.add("hidden"), 2000);
                    }
                } else if (data.type === "edit_message") {
                    const targetChat = data.is_group ? data.recipient_id : (data.sender_id === currentUser ? activeContact : data.sender_id);
                    if (chatHistories[targetChat]) {
                        const m = chatHistories[targetChat].find(item => item.id === data.id);
                        if (m) { m.content = data.new_content; m.is_edited = 1; }
                        if (activeContact === targetChat) renderMessages();
                    }
                } else if (data.type === "reaction") {
                    if (chatHistories[activeContact]) {
                        const m = chatHistories[activeContact].find(item => item.id === data.id);
                        if (m) m.reactions = data.reactions;
                        renderMessages();
                    }
                } else if (data.type === "pin_message") {
                    if (chatHistories[activeContact]) {
                        chatHistories[activeContact].forEach(item => item.is_pinned = (item.id === data.id ? data.is_pinned : 0));
                        renderMessages();
                    }
                } else if (data.type === "poll_create" || data.type === "poll_update") {
                    if (data.type === "poll_create") activePolls[data.poll_id] = data;
                    else if (activePolls[data.poll_id]) activePolls[data.poll_id].votes = data.votes;
                    renderMessages();
                } else if (data.is_group) {
                    const gId = data.group_id;
                    if (!chatHistories[gId]) chatHistories[gId] = [];
                    if (!chatHistories[gId].some(m => m.id === data.id)) {
                        chatHistories[gId].push(data);
                    }
                    if (activeContact === gId) renderMessages();
                } else if (data.type === "call_request") {
                    activeCallPartner = data.sender_id;
                    currentCallType = data.call_type;
                    document.getElementById("callPartnerLabel").innerText = `Incoming ${data.call_type} call from ${activeCallPartner}`;
                    document.getElementById("call-modal").classList.remove("hidden");
                    await setupWebRTCConnection(false);
                } else if (data.type === "offer" || data.type === "answer" || data.type === "ice_candidate") {
                    if (data.offer && peerConnection) {
                        await peerConnection.setRemoteDescription(new RTCSessionDescription(data.offer));
                        const answer = await peerConnection.createAnswer();
                        await peerConnection.setLocalDescription(answer);
                        ws.send(JSON.stringify({ type: "answer", recipient_id: data.sender_id, answer: answer }));
                    } else if (data.answer && peerConnection) {
                        await peerConnection.setRemoteDescription(new RTCSessionDescription(data.answer));
                    } else if (data.candidate && peerConnection) {
                        await peerConnection.addIceCandidate(new RTCIceCandidate(data.candidate));
                    }
                } else if (data.type === "end_call") {
                    closeCallModals();
                } else {
                    const sender = data.sender_id === currentUser ? activeContact : data.sender_id;
                    if (!chatHistories[sender]) chatHistories[sender] = [];
                    if (!chatHistories[sender].some(m => m.id === data.id)) {
                        chatHistories[sender].push(data);
                    }
                    if (activeContact === sender) renderMessages();
                    renderContacts();
                }
            };

            ws.onclose = function() {
                setTimeout(connectWebSocket, 3000);
            };
        }

        function sendTypingSignal() {
            if (!activeContact) return;
            ws.send(JSON.stringify({ type: "typing", recipient_id: activeContact, is_group: isGroupActive }));
        }

        function renderContacts(filter = "") {
            const container = document.getElementById("contactsListContainer");
            container.innerHTML = "";
            userGroups.forEach(grp => {
                if (!grp.group_name.toLowerCase().includes(filter.toLowerCase())) return;
                const isActive = activeContact === grp.group_id ? "active" : "";
                container.innerHTML += `
                    <div class="contact-item ${isActive}" onclick="selectGroup('${grp.group_id}', '${grp.group_name}')">
                        <div class="contact-avatar">👥</div>
                        <div class="contact-details"><h4>${grp.group_name}</h4><p>Group (${grp.members.length})</p></div>
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
                        <div class="contact-avatar">${email.charAt(0).toUpperCase()}<div class="${isOnline ? 'online-dot' : 'offline-dot'}"></div></div>
                        <div class="contact-details"><h4>${email}</h4><p>${isOnline ? 'Online' : 'Offline'}</p></div>
                    </div>
                `;
            });
        }

        function filterContacts() { renderContacts(document.getElementById("searchContactInput").value); }

        async function selectContact(email) {
            activeContact = email;
            isGroupActive = false;
            
            let contactAvatarHtml = email.charAt(0).toUpperCase();
            let contactStatusText = onlineUsers.includes(email) ? "Online" : "Offline";
            try {
                const resUser = await fetch(`/user/${encodeURIComponent(email)}`);
                const userData = await resUser.json();
                if (userData.profile_pic) {
                    contactAvatarHtml = `<img src="${userData.profile_pic}">`;
                }
                if (userData.status) {
                    contactStatusText = userData.status;
                }
            } catch(e) {}

            document.getElementById("activeChatTitle").innerText = email;
            document.getElementById("activeChatStatus").innerText = contactStatusText;
            document.getElementById("activeChatAvatar").innerHTML = contactAvatarHtml;
            document.getElementById("messageInput").disabled = false;
            document.getElementById("app-container").classList.add("mobile-chat-open");
            
            await fetchPollsForChat(email);
            const res = await fetch(`/history/${encodeURIComponent(currentUser)}/${encodeURIComponent(email)}`);
            const data = await res.json();
            chatHistories[email] = data.history.map(m => ({ ...m, sender: m.sender }));
            renderMessages();
        }

        async function selectGroup(groupId, groupName) {
            activeContact = groupId;
            isGroupActive = true;
            document.getElementById("activeChatTitle").innerText = groupName;
            document.getElementById("activeChatStatus").innerText = "Group Chat";
            document.getElementById("activeChatAvatar").innerHTML = "👥";
            document.getElementById("messageInput").disabled = false;
            document.getElementById("app-container").classList.add("mobile-chat-open");

            await fetchPollsForChat(groupId);
            const res = await fetch(`/group-history/${groupId}`);
            const data = await res.json();
            chatHistories[groupId] = data.history.map(m => ({ ...m, sender: m.sender }));
            renderMessages();
        }

        function returnToSidebar(e) {
            e.stopPropagation();
            document.getElementById("app-container").classList.remove("mobile-chat-open");
            activeContact = null;
        }

        async function openContactProfile() {
            if (!activeContact || isGroupActive) return;
            let avatarHtml = activeContact.charAt(0).toUpperCase();
            let statusText = onlineUsers.includes(activeContact) ? "Online" : "Offline";
            try {
                const res = await fetch(`/user/${encodeURIComponent(activeContact)}`);
                const data = await res.json();
                if (data.profile_pic) avatarHtml = `<img src="${data.profile_pic}">`;
                if (data.status) statusText = data.status;
            } catch(e) {}

            document.getElementById("modalProfileName").innerText = activeContact;
            document.getElementById("modalProfileStatus").innerText = statusText;
            document.getElementById("modalProfileAvatar").innerHTML = avatarHtml;
            
            const btnWrapper = document.getElementById("addToContactsBtnWrapper");
            if (savedContacts.includes(activeContact)) {
                btnWrapper.innerHTML = `<p style="color: #10b981; font-weight: 600; margin-top: 15px;">✓ Saved in Contacts</p>`;
            } else {
                btnWrapper.innerHTML = `<button class="sel-btn" style="width: 100%; background: var(--accent-gradient); color: white; margin-top: 15px;" onclick="addCurrentContactPermanent()">➕ Add to Contacts</button>`;
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
            new Set([...onlineUsers, ...savedContacts]).forEach(email => {
                if (email === currentUser) return;
                listContainer.innerHTML += `<label style="display: flex; gap: 8px; margin-bottom: 6px; font-size: 13px;"><input type="checkbox" class="group-member-checkbox" value="${email}"> ${email}</label>`;
            });
            document.getElementById("group-modal").classList.remove("hidden");
        }
        function closeGroupModal() { document.getElementById("group-modal").classList.add("hidden"); }

        async function createGroupSubmit() {
            const groupName = document.getElementById("groupNameInput").value.trim();
            const members = Array.from(document.querySelectorAll(".group-member-checkbox:checked")).map(cb => cb.value);
            if (!groupName || members.length === 0) return alert("Provide a name and members.");
            const res = await fetch("/groups/create", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ group_name: groupName, admin: currentUser, members: members })
            });
            userGroups.push(await res.json());
            closeGroupModal();
            renderContacts();
        }

        function editMessage(msgId, currentText) {
            const newText = prompt("Edit message:", currentText);
            if (newText && newText !== currentText) {
                ws.send(JSON.stringify({ type: "edit_message", recipient_id: activeContact, msg_id: msgId, new_content: newText, is_group: isGroupActive }));
            }
        }

        function sendReaction(msgId, emoji) {
            ws.send(JSON.stringify({ type: "reaction", recipient_id: activeContact, msg_id: msgId, emoji: emoji, is_group: isGroupActive }));
        }

        function togglePin(msgId, currentState) {
            const nextState = currentState ? 0 : 1;
            ws.send(JSON.stringify({ type: "pin_message", recipient_id: activeContact, msg_id: msgId, pin_state: nextState, is_group: isGroupActive }));
        }

        function openPollModal() { document.getElementById("poll-modal").classList.remove("hidden"); }
        function closePollModal() { document.getElementById("poll-modal").classList.add("hidden"); }

        function submitPoll() {
            const question = document.getElementById("pollQuestionInput").value.trim();
            const options = document.getElementById("pollOptionsInput").value.split(',').map(o => o.trim()).filter(Boolean);
            if (!question || options.length < 2) return alert("Enter question and at least 2 options.");
            ws.send(JSON.stringify({ type: "poll_create", recipient_id: activeContact, question: question, options: options, is_group: isGroupActive }));
            closePollModal();
        }

        function votePoll(pollId, option) {
            ws.send(JSON.stringify({ type: "poll_vote", recipient_id: activeContact, poll_id: pollId, option: option, is_group: isGroupActive }));
        }

        async function toggleVoiceRecording() {
            const btn = document.getElementById("voiceRecordBtn");
            const viewOnce = document.getElementById("viewOnceCheckbox").checked;
            if (!isRecording) {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];
                mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
                mediaRecorder.onstop = () => {
                    const reader = new FileReader();
                    reader.onload = () => {
                        ws.send(JSON.stringify({ type: "audio_note", recipient_id: activeContact, message: reader.result, view_once: viewOnce, is_group: isGroupActive }));
                    };
                    reader.readAsDataURL(new Blob(audioChunks, { type: 'audio/webm' }));
                };
                mediaRecorder.start();
                isRecording = true;
                btn.classList.add("recording");
            } else {
                mediaRecorder.stop();
                isRecording = false;
                btn.classList.remove("recording");
                document.getElementById("viewOnceCheckbox").checked = false;
            }
        }

        async function startCall(type) {
            if (!activeContact || isGroupActive) return;
            currentCallType = type;
            activeCallPartner = activeContact;
            ws.send(JSON.stringify({ type: "call_request", recipient_id: activeCallPartner, call_type: type }));
            document.getElementById("callPartnerLabel").innerText = `Calling ${activeCallPartner}...`;
            document.getElementById("call-modal").classList.remove("hidden");
            await setupWebRTCConnection(true);
        }

        async function setupWebRTCConnection(isInitiator) {
            localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: currentCallType === 'video' });
            document.getElementById("video-local").srcObject = localStream;
            peerConnection = new RTCPeerConnection(rtcConfig);
            localStream.getTracks().forEach(track => peerConnection.addTrack(track, localStream));
            peerConnection.ontrack = e => document.getElementById("remoteVideo").srcObject = e.streams[0];
            peerConnection.onicecandidate = e => {
                if (e.candidate) ws.send(JSON.stringify({ type: "ice_candidate", recipient_id: activeCallPartner, candidate: e.candidate }));
            };
            if (isInitiator) {
                const offer = await peerConnection.createOffer();
                await peerConnection.setLocalDescription(offer);
                ws.send(JSON.stringify({ type: "offer", recipient_id: activeCallPartner, offer: offer }));
            }
        }

        function toggleAudioMute() {
            if (!localStream) return;
            const audioTrack = localStream.getAudioTracks()[0];
            if (audioTrack) {
                audioTrack.enabled = !audioTrack.enabled;
                document.getElementById("muteAudioBtn").innerText = audioTrack.enabled ? "🎤 Mute" : "🎤 Unmute";
            }
        }

        function toggleVideoFeed() {
            if (!localStream) return;
            const videoTrack = localStream.getVideoTracks()[0];
            if (videoTrack) {
                videoTrack.enabled = !videoTrack.enabled;
                document.getElementById("toggleVideoBtn").innerText = videoTrack.enabled ? "📹 Stop Video" : "📹 Start Video";
            }
        }

        async function startScreenShare() {
            try {
                const screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true });
                const screenTrack = screenStream.getVideoTracks()[0];
                const sender = peerConnection.getSenders().find(s => s.track && s.track.kind === 'video');
                if (sender) sender.replaceTrack(screenTrack);
                screenTrack.onended = () => {
                    const cameraTrack = localStream.getVideoTracks()[0];
                    if (sender && cameraTrack) sender.replaceTrack(cameraTrack);
                };
            } catch (err) { console.error("Screen share error:", err); }
        }

        function endCall() {
            if (activeCallPartner) ws.send(JSON.stringify({ type: "end_call", recipient_id: activeCallPartner }));
            closeCallModals();
        }

        function closeCallModals() {
            document.getElementById("call-modal").classList.add("hidden");
            if (localStream) { localStream.getTracks().forEach(t => t.stop()); localStream = null; }
            if (peerConnection) { peerConnection.close(); peerConnection = null; }
            activeCallPartner = null;
        }

        function filterChatMessages() {
            renderMessages();
        }

        function renderMessages() {
            const container = document.getElementById("chatMessagesContainer");
            container.innerHTML = "";
            const messages = chatHistories[activeContact] || [];
            const filterQuery = document.getElementById("messageSearchInput").value.toLowerCase();
            
            let pinnedText = null;

            messages.forEach(msg => {
                if (filterQuery && !msg.content.toLowerCase().includes(filterQuery)) return;
                if (msg.is_pinned) pinnedText = msg.content;
                const isOutgoing = msg.sender === currentUser;
                let contentHTML = "";

                if (msg.view_once) {
                    contentHTML = `<div style="background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; font-size: 12px;">🔒 View Once Media (Opened)</div>`;
                } else if (msg.type === "image") {
                    contentHTML = `<img src="${msg.content}" style="max-width: 200px; border-radius: 8px;">`;
                } else if (msg.type === "audio_note") {
                    contentHTML = `<audio controls src="${msg.content}" style="max-width: 200px; height: 32px;"></audio>`;
                } else {
                    contentHTML = `<span>${msg.content}</span> ${msg.is_edited ? '<span style="font-size:10px; opacity:0.7;">(edited)</span>' : ''}`;
                }

                let reactionsHTML = "";
                if (msg.reactions && Object.keys(msg.reactions).length > 0) {
                    const uniqueEmojis = [...new Set(Object.values(msg.reactions))].join(' ');
                    reactionsHTML = `<div class="reactions-badge">${uniqueEmojis}</div>`;
                }

                let actionsRow = `
                    <div class="msg-actions-row">
                        <button class="msg-action-btn" onclick="sendReaction(${msg.id}, '👍')">👍</button>
                        <button class="msg-action-btn" onclick="sendReaction(${msg.id}, '❤️')">❤️</button>
                        <button class="msg-action-btn" onclick="togglePin(${msg.id}, ${msg.is_pinned})">📌</button>
                        ${isOutgoing && msg.type === 'chat' ? `<button class="msg-action-btn" onclick="editMessage(${msg.id}, '${msg.content}')">✏️</button>` : ''}
                        <span style="margin-left: auto; font-size: 10px; opacity: 0.8;">✓✓</span>
                    </div>
                `;

                container.innerHTML += `
                    <div class="message ${isOutgoing ? "outgoing" : "incoming"}">
                        ${isGroupActive && !isOutgoing ? `<span style="font-size:11px; font-weight:bold; color:var(--accent);">${msg.sender}</span>` : ''}
                        <div>${contentHTML}</div>
                        ${reactionsHTML}
                        ${actionsRow}
                    </div>
                `;
            });

            Object.values(activePolls).forEach(poll => {
                let totalVotes = Object.values(poll.votes).reduce((a, b) => a + b.length, 0);
                let optionsHtml = "";
                for (let [opt, voters] of Object.entries(poll.votes)) {
                    let percent = totalVotes > 0 ? Math.round((voters.length / totalVotes) * 100) : 0;
                    optionsHtml += `
                        <div onclick="votePoll('${poll.poll_id}', '${opt}')" style="background: var(--bg-secondary); padding: 8px; border-radius: 6px; margin-top: 4px; cursor: pointer; border: 1px solid var(--border);">
                            <div style="display: flex; justify-content: space-between; font-size: 13px;"><span>${opt}</span> <span>${voters.length} votes (${percent}%)</span></div>
                            <div style="background: var(--accent); height: 4px; width: ${percent}%; border-radius: 2px; margin-top: 4px;"></div>
                        </div>
                    `;
                }
                container.innerHTML += `
                    <div class="message incoming" style="width: 100%; max-width: 350px;">
                        <h4 style="color: var(--accent); font-size: 14px; margin-bottom: 6px;">📊 ${poll.question}</h4>
                        ${optionsHtml}
                    </div>
                `;
            });

            const banner = document.getElementById("pinned-banner");
            if (pinnedText) {
                document.getElementById("pinnedBannerText").innerText = pinnedText;
                banner.classList.remove("hidden");
            } else {
                banner.classList.add("hidden");
            }

            container.scrollTop = container.scrollHeight;
        }

        function sendMessage() {
            const input = document.getElementById("messageInput");
            const text = input.value.trim();
            const viewOnce = document.getElementById("viewOnceCheckbox").checked;
            if (!text || !activeContact) return;

            ws.send(JSON.stringify({ type: "chat", recipient_id: activeContact, message: text, view_once: viewOnce, is_group: isGroupActive }));
            input.value = "";
            document.getElementById("viewOnceCheckbox").checked = false;
        }

        function handleKey(e) { if (e.key === "Enter") sendMessage(); }

        function sendImage(e) {
            const file = e.target.files[0];
            const viewOnce = document.getElementById("viewOnceCheckbox").checked;
            if (!file || !activeContact) return;
            const reader = new FileReader();
            reader.onload = function() {
                ws.send(JSON.stringify({ type: "image", recipient_id: activeContact, message: reader.result, view_once: viewOnce, is_group: isGroupActive }));
                document.getElementById("viewOnceCheckbox").checked = false;
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
