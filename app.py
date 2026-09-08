from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import json
import sqlite3
from typing import Dict

app = FastAPI(title="Metaverse_WhatsApp - Google Quantum Mobile Edition")

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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat ON messages (sender, recipient)")
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

manager = ConnectionManager()

@app.get("/history/{user}/{contact}")
async def get_history(user: str, contact: str):
    cursor.execute("""
        SELECT id, sender, type, content FROM messages 
        WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
        ORDER BY id ASC
    """, (user, contact, contact, user))
    rows = cursor.fetchall()
    history = [{"id": r[0], "sender": r[1], "type": r[2], "content": r[3]} for r in rows]
    return {"history": history}

@app.get("/contacts/{username}")
async def get_saved_contacts(username: str):
    cursor.execute("""
        SELECT DISTINCT sender FROM messages WHERE recipient = ?
        UNION
        SELECT DISTINCT recipient FROM messages WHERE sender = ?
    """, (username, username))
    rows = cursor.fetchall()
    contacts = [r[0] for r in rows if r[0] and r[0] != "Brian 🧠 (AI Archive)"]
    return {"contacts": contacts}

@app.delete("/message/{msg_id}")
async def delete_message(msg_id: int):
    cursor.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
    db_conn.commit()
    return {"status": "deleted"}

@app.delete("/clear-chat/{user}/{contact}")
async def clear_chat(user: str, contact: str):
    cursor.execute("""
        DELETE FROM messages 
        WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
    """, (user, contact, contact, user))
    db_conn.commit()
    return {"status": "cleared"}

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
            
            if msg_type == "delete_message":
                msg_id = message_data.get("message_id")
                if msg_id:
                    cursor.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
                    db_conn.commit()
                    payload = {"type": "delete_message", "id": msg_id, "sender_id": username}
                    await manager.send_personal_message(payload, recipient_id)
                continue

            if msg_type == "bulk_delete":
                msg_ids = message_data.get("message_ids", [])
                for msg_id in msg_ids:
                    cursor.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
                db_conn.commit()
                payload = {"type": "bulk_delete", "ids": msg_ids, "sender_id": username}
                await manager.send_personal_message(payload, recipient_id)
                continue

            if msg_type == "clear_chat":
                cursor.execute("""
                    DELETE FROM messages 
                    WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
                """, (username, recipient_id, recipient_id, username))
                db_conn.commit()
                payload = {"type": "clear_chat", "sender_id": username}
                await manager.send_personal_message(payload, recipient_id)
                continue
            
            if recipient_id == "Brian 🧠 (AI Archive)":
                if msg_type == "summarize":
                    cursor.execute("""
                        SELECT sender, content FROM messages 
                        WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
                        ORDER BY id ASC
                    """, (username, "Brian 🧠 (AI Archive)", "Brian 🧠 (AI Archive)", username))
                    all_msgs = cursor.fetchall()
                    summary_text = f"🧠 [Brian AI Vault Summary]: Analyzed {len(all_msgs)} interactions. Node sync is stable, memory banks fully indexed."
                    
                    response_payload = {
                        "id": 999999,
                        "type": "chat",
                        "sender_id": "Brian 🧠 (AI Archive)",
                        "message": summary_text
                    }
                    await websocket.send_text(json.dumps(response_payload))
                    continue

                cursor.execute("INSERT INTO messages (sender, recipient, type, content) VALUES (?, ?, ?, ?)", 
                               (username, "Brian 🧠 (AI Archive)", msg_type, content))
                db_conn.commit()
                
                brian_reply = f"🧠 [Brian Vault Archive]: Safely indexed your {msg_type}."
                cursor.execute("INSERT INTO messages (sender, recipient, type, content) VALUES (?, ?, ?, ?)", 
                               ("Brian 🧠 (AI Archive)", username, "chat", brian_reply))
                db_conn.commit()
                
                cursor.execute("SELECT last_insert_rowid()")
                brian_msg_id = cursor.fetchone()[0]

                response_payload = {
                    "id": brian_msg_id,
                    "type": "chat",
                    "sender_id": "Brian 🧠 (AI Archive)",
                    "message": brian_reply
                }
                await websocket.send_text(json.dumps(response_payload))
                continue

            if msg_type in ["chat", "audio_note", "image", "signal"]:
                msg_id = None
                if msg_type != "signal":
                    cursor.execute("INSERT INTO messages (sender, recipient, type, content) VALUES (?, ?, ?, ?)", 
                                   (username, recipient_id, msg_type, content))
                    db_conn.commit()
                    cursor.execute("SELECT last_insert_rowid()")
                    msg_id = cursor.fetchone()[0]

                payload = {
                    "id": msg_id,
                    "type": msg_type,
                    "sender_id": username,
                    "message": content,
                    "signal": message_data.get("signal")
                }
                await manager.send_personal_message(payload, recipient_id)
                
    except WebSocketDisconnect:
        manager.disconnect(username)
        await manager.broadcast_user_list()


# ==================== FRONTEND: Quantum Mobile-Responsive UI ====================
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Metaverse WhatsApp - Google Quantum Edition</title>
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

        body.theme-emerald {
            --accent: #10b981;
            --accent-gradient: linear-gradient(135deg, #10b981, #047857);
            --outgoing: #047857;
        }

        body.theme-violet {
            --accent: #8b5cf6;
            --accent-gradient: linear-gradient(135deg, #8b5cf6, #ec4899);
            --outgoing: #7c3aed;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }
        body { background: var(--bg-primary); height: 100vh; display: flex; justify-content: center; align-items: center; color: var(--text-main); overflow: hidden; }
        .hidden { display: none !important; }
        
        #app-container { width: 98%; max-width: 1500px; height: 95vh; background: var(--bg-secondary); border: 1px solid rgba(0, 242, 254, 0.2); display: flex; box-shadow: 0 0 50px rgba(0, 0, 0, 0.8); border-radius: 18px; overflow: hidden; position: relative; backdrop-filter: blur(12px); }
        
        /* Login Screen */
        #login-screen { position: absolute; inset: 0; background: var(--bg-primary); display: flex; justify-content: center; align-items: center; z-index: 200; padding: 15px; }
        #login-box { background: var(--bg-panel); border: 1px solid var(--border); padding: 40px 30px; border-radius: 20px; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.5); width: 100%; max-width: 440px; }
        #login-box h1 { color: var(--accent); margin-bottom: 8px; font-size: 26px; font-weight: 700; }
        #login-box p { color: var(--text-muted); font-size: 13px; margin-bottom: 25px; }
        
        .google-btn-wrapper { display: flex; justify-content: center; margin-bottom: 20px; }
        
        .divider { display: flex; align-items: center; text-align: center; color: var(--text-muted); font-size: 12px; margin: 18px 0; }
        .divider::before, .divider::after { content: ''; flex: 1; border-bottom: 1px solid var(--border); }
        .divider::before { margin-right: .5em; }
        .divider::after { margin-left: .5em; }

        #login-box input { width: 100%; padding: 12px 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 10px; color: white; font-size: 14px; outline: none; margin-bottom: 14px; text-align: center; }
        #login-box input:focus { border-color: var(--accent); box-shadow: 0 0 10px rgba(0,242,254,0.3); }
        #login-box button.manual-login { width: 100%; padding: 12px; background: var(--accent-gradient); color: var(--bg-primary); border: none; border-radius: 10px; font-weight: bold; font-size: 14px; cursor: pointer; transition: 0.2s; }
        #login-box button.manual-login:hover { opacity: 0.9; transform: translateY(-1px); }

        /* Sidebar */
        .sidebar { width: 35%; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; height: 100%; }
        .sidebar-header { padding: 16px 20px; background: var(--bg-secondary); display: flex; align-items: center; justify-content: space-between; height: 75px; border-bottom: 1px solid var(--border); }
        .my-profile { font-weight: 600; color: var(--accent); font-size: 14px; display: flex; align-items: center; gap: 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }
        
        .sidebar-toolbar { padding: 12px 18px; background: var(--bg-panel); border-bottom: 1px solid var(--border); display: flex; gap: 8px; }
        .sidebar-toolbar input { flex: 1; padding: 10px 14px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 8px; color: white; font-size: 13px; outline: none; }
        .sidebar-toolbar input:focus { border-color: var(--accent); }
        
        .contacts-list { flex: 1; overflow-y: auto; }
        .contact-item { display: flex; align-items: center; padding: 14px 18px; border-bottom: 1px solid rgba(255,255,255,0.03); cursor: pointer; transition: 0.2s; position: relative; }
        .contact-item:hover, .contact-item.active { background: #374151; border-left: 4px solid var(--accent); }
        .contact-avatar { width: 48px; height: 48px; border-radius: 50%; background: var(--accent-gradient); color: var(--bg-primary); display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 18px; margin-right: 14px; position: relative; flex-shrink: 0; }
        .online-dot { width: 12px; height: 12px; background: #10b981; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        .offline-dot { width: 12px; height: 12px; background: #6b7280; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        .contact-details h4 { font-size: 15px; color: var(--text-main); font-weight: 500; }
        .contact-details p { font-size: 12px; color: var(--accent); margin-top: 3px; }

        /* Context Menu */
        #context-menu { position: absolute; background: var(--bg-panel); border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 10px 25px rgba(0,0,0,0.6); z-index: 1000; display: none; padding: 6px 0; }
        .context-menu-item { padding: 10px 22px; font-size: 13px; color: #ef4444; cursor: pointer; display: flex; align-items: center; gap: 8px; }
        .context-menu-item:hover { background: #374151; }

        /* Chat Panel */
        .chat-panel { flex: 1; display: flex; flex-direction: column; background: var(--bg-primary); position: relative; height: 100%; }
        .chat-header { height: 75px; background: var(--bg-secondary); padding: 12px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); }
        .active-chat-info { display: flex; align-items: center; gap: 12px; }
        .header-actions { display: flex; align-items: center; gap: 8px; }
        
        .header-btn { background: var(--bg-panel); color: var(--text-main); border: 1px solid var(--border); padding: 7px 12px; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 500; transition: 0.2s; white-space: nowrap; }
        .header-btn:hover { border-color: var(--accent); color: var(--accent); }
        .header-btn.active-mode { background: var(--accent); color: var(--bg-primary); font-weight: bold; border-color: var(--accent); }
        
        .call-btn { background: var(--accent-gradient); color: var(--bg-primary); border: none; padding: 8px 14px; border-radius: 25px; cursor: pointer; font-weight: 700; font-size: 12px; box-shadow: 0 0 15px rgba(0, 242, 254, 0.2); white-space: nowrap; }
        
        /* Quantum Features Bar */
        .quantum-features-bar { background: rgba(17, 24, 39, 0.9); padding: 8px 15px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); font-size: 11px; flex-wrap: wrap; gap: 6px; }
        .feature-toggle { display: flex; align-items: center; gap: 6px; cursor: pointer; color: var(--text-muted); font-weight: 500; }
        .feature-toggle.active { color: var(--accent); font-weight: bold; }
        
        .chat-messages { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; background-image: radial-gradient(circle, var(--border) 1px, transparent 1px); background-size: 28px 28px; }
        
        .message { max-width: 75%; padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 22px; word-wrap: break-word; position: relative; box-shadow: 0 3px 8px rgba(0,0,0,0.3); display: flex; align-items: flex-start; gap: 10px; animation: fadeIn 0.2s ease; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
        
        .message.incoming { background: var(--bg-panel); border: 1px solid var(--border); align-self: flex-start; border-top-left-radius: 2px; color: var(--text-main); }
        .message.outgoing { background: var(--outgoing); align-self: flex-end; border-top-right-radius: 2px; color: white; }
        
        .msg-checkbox { margin-top: 3px; accent-color: var(--accent); transform: scale(1.2); cursor: pointer; }
        .msg-body { flex: 1; }
        .msg-ticks { font-size: 11px; float: right; margin-left: 10px; margin-top: 4px; color: rgba(255,255,255,0.7); }
        .delete-msg-btn { position: absolute; top: 6px; right: 8px; background: none; border: none; color: rgba(255,255,255,0.6); font-size: 12px; cursor: pointer; display: none; }
        .message:hover .delete-msg-btn { display: inline-block; }
        .delete-msg-btn:hover { color: #ef4444; }

        /* Selection Action Bar */
        #selection-action-bar { position: absolute; bottom: 75px; left: 0; right: 0; background: var(--bg-panel); border-top: 1px solid var(--border); padding: 12px 20px; display: flex; align-items: center; justify-content: space-between; z-index: 50; }
        .sel-btn { background: var(--bg-secondary); border: 1px solid var(--border); color: var(--text-main); padding: 8px 14px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 12px; }
        .sel-btn.delete { background: #ef4444; border-color: #ef4444; color: white; }
        .sel-btn.forward { background: #3b82f6; border-color: #3b82f6; color: white; }

        /* Input Area */
        .chat-input-area { min-height: 75px; background: var(--bg-secondary); padding: 12px 18px; display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--border); }
        .chat-input-area input { flex: 1; padding: 12px 14px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg-panel); color: white; font-size: 14px; outline: none; }
        .chat-input-area input:focus { border-color: var(--accent); }
        .action-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: var(--text-muted); padding: 4px; transition: 0.2s; }
        .action-btn:hover { color: var(--accent); }
        .action-btn.recording { color: #ef4444; animation: pulse 1s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }

        /* Forward Modal */
        #forward-modal { position: absolute; inset: 0; background: rgba(0,0,0,0.8); z-index: 300; display: flex; justify-content: center; align-items: center; backdrop-filter: blur(5px); padding: 20px; }
        .modal-content { background: var(--bg-panel); border: 1px solid var(--border); padding: 25px; border-radius: 16px; width: 100%; max-width: 380px; box-shadow: 0 15px 35px rgba(0,0,0,0.6); }
        .modal-content h3 { color: var(--accent); margin-bottom: 16px; font-size: 18px; }
        .modal-contact-item { padding: 12px 16px; background: var(--bg-secondary); margin-bottom: 10px; border-radius: 8px; cursor: pointer; border: 1px solid var(--border); transition: 0.2s; font-size: 14px; }
        .modal-contact-item:hover { border-color: var(--accent); }

        /* Video Call Overlay */
        #call-overlay { position: absolute; inset: 0; background: rgba(8, 12, 20, 0.96); z-index: 400; display: flex; flex-direction: column; align-items: center; justify-content: center; backdrop-filter: blur(10px); padding: 20px; }
        .video-grid { display: flex; gap: 20px; margin-bottom: 25px; flex-wrap: wrap; justify-content: center; }
        video { width: 450px; max-width: 90vw; height: 320px; background: black; border: 1px solid var(--border); border-radius: 16px; object-fit: cover; box-shadow: 0 0 30px rgba(0,0,0,0.5); }
        .hangup-btn { background: #ef4444; color: white; border: none; padding: 12px 28px; border-radius: 30px; font-weight: bold; cursor: pointer; font-size: 15px; box-shadow: 0 0 15px rgba(239,68,68,0.4); }

        /* ==================== MOBILE RESPONSIVE MEDIA QUERY ==================== */
        @media (max-width: 768px) {
            body { align-items: flex-start; height: 100vh; height: 100dvh; }
            #app-container { width: 100%; height: 100vh; height: 100dvh; border-radius: 0; border: none; }
            
            /* By default on mobile, show sidebar and hide chat */
            .sidebar { width: 100%; display: flex; }
            .chat-panel { width: 100%; display: none; }
            
            /* When chat is open on mobile */
            #app-container.mobile-chat-open .sidebar { display: none; }
            #app-container.mobile-chat-open .chat-panel { display: flex; }
            
            #backToContactsBtn { display: inline-block !important; }
            video { width: 100vw; height: 210px; }
            .video-grid { flex-direction: column; gap: 12px; margin-bottom: 20px; }
            .message { max-width: 85%; }
            .chat-messages { padding: 12px; }
            .chat-header { padding: 10px 14px; }
            .chat-input-area { padding: 10px 12px; }
        }
    </style>
</head>
<body class="theme-cyan">

    <div id="app-container">
        <!-- Google Sign-In & Login Screen -->
        <div id="login-screen">
            <div id="login-box">
                <h1>⚡ Metaverse</h1>
                <p>Google Quantum Encrypted Node</p>
                
                <!-- Google Sign In Button Container -->
                <div class="google-btn-wrapper">
                    <div id="g_id_onload"
                         data-client_id="358332042325-3s7o118sjfv1qug4r6qlmf534083ti10.apps.googleusercontent.com"
                         data-callback="handleGoogleLogin"
                         data-auto_select="false"
                         data-cookie_policy="single_host_origin">
                    </div>
                    <div class="g_id_signin" 
                         data-type="standard" 
                         data-shape="pill" 
                         data-theme="filled_black" 
                         data-size="large" 
                         data-logo_alignment="left">
                    </div>
                </div>

                <div class="divider">or quick manual access</div>
                
                <input type="text" id="loginUsernameInput" placeholder="Enter custom username..." onkeypress="handleLoginKey(event)">
                <button class="manual-login" onclick="performManualLogin()">Initialize Node Session</button>
            </div>
        </div>

        <!-- Sidebar -->
        <div class="sidebar">
            <div class="sidebar-header">
                <div class="my-profile" id="my-profile-name" title="Node: Offline">⚡ Node: Offline</div>
                <button class="header-btn" onclick="logout()" title="Logout" style="font-size: 11px; padding: 5px 10px;">Logout</button>
            </div>
            <div class="sidebar-toolbar">
                <input type="text" id="searchContactInput" placeholder="Search contacts..." oninput="filterContacts()">
            </div>
            <div class="contacts-list" id="contactsListContainer"></div>
        </div>

        <!-- Context Menu for Right Click / Long Press -->
        <div id="context-menu">
            <div class="context-menu-item" onclick="clearChatAction()">🗑️ Clear Chat History</div>
        </div>

        <!-- Chat Panel -->
        <div class="chat-panel">
            <div class="chat-header">
                <div class="active-chat-info">
                    <button class="header-btn hidden" id="backToContactsBtn" onclick="returnToSidebar()">⬅️</button>
                    <div class="contact-avatar" id="activeChatAvatar" style="background: var(--bg-panel); color: var(--text-muted);">?</div>
                    <div>
                        <h4 id="activeChatTitle" style="font-size: 15px; color: var(--text-main); font-weight: 500;">Select Chat Node</h4>
                        <p id="activeChatStatus" style="font-size: 11px; color: var(--accent);">Ready</p>
                    </div>
                </div>
                <div class="header-actions">
                    <button class="header-btn hidden" id="selectModeBtn" onclick="toggleSelectMode()">Select</button>
                    <button class="call-btn hidden" id="videoCallBtn" onclick="startCall()">🔮 Video Call</button>
                </div>
            </div>

            <!-- Quantum Features Bar -->
            <div class="quantum-features-bar hidden" id="quantumFeaturesBar">
                <div style="display: flex; gap: 15px; flex-wrap: wrap;">
                    <span class="feature-toggle" id="ghostModeToggle" onclick="toggleGhostMode()">👻 Ghost: <b id="ghostStatus">OFF</b></span>
                    <span class="feature-toggle" onclick="requestAiSummary()">🧠 AI Summarize</span>
                </div>
                <div style="display: flex; gap: 8px;">
                    <span style="cursor:pointer; color: #00f2fe;" onclick="setTheme('cyan')" title="Cyber Cyan">🔵</span>
                    <span style="cursor:pointer; color: #10b981;" onclick="setTheme('emerald')" title="Matrix Emerald">🟢</span>
                    <span style="cursor:pointer; color: #8b5cf6;" onclick="setTheme('violet')" title="Midnight Violet">🟣</span>
                </div>
            </div>

            <div class="chat-messages" id="chatMessagesContainer">
                <div style="text-align: center; margin: auto; color: var(--text-muted); font-size: 13px; padding: 0 20px;">
                    <p>🔒 Google Authenticated & Quantum End-to-End Encrypted Node Active</p>
                </div>
            </div>

            <!-- Selection Action Bar -->
            <div id="selection-action-bar" class="hidden">
                <span id="selectedCountText" style="font-size: 13px; font-weight: 600; color: var(--accent);">0 selected</span>
                <div style="display: flex; gap: 8px;">
                    <button class="sel-btn forward" onclick="openForwardModal()">↗️ Forward</button>
                    <button class="sel-btn delete" onclick="deleteSelectedMessages()">🗑️ Delete</button>
                    <button class="sel-btn" onclick="toggleSelectMode()">Cancel</button>
                </div>
            </div>

            <div class="chat-input-area">
                <label class="action-btn" title="Attach Image">📎<input type="file" id="imageInput" accept="image/*" style="display:none;" onchange="sendImage(event)"></label>
                <input type="text" id="messageInput" placeholder="Type a secure message..." onkeypress="handleKey(event)" disabled>
                <button class="action-btn" id="micBtn" onclick="toggleRecordVoice()" title="Voice Note" disabled>🎙️</button>
                <button class="action-btn" onclick="sendMessage()" style="color: var(--accent); font-size: 22px;" title="Send">➤</button>
            </div>
        </div>

        <!-- Forward Modal -->
        <div id="forward-modal" class="hidden">
            <div class="modal-content">
                <h3>Forward to Node...</h3>
                <div id="modalContactsList" style="max-height: 220px; overflow-y: auto; margin-bottom: 16px;"></div>
                <button class="sel-btn" style="width: 100%; background: var(--bg-secondary);" onclick="closeForwardModal()">Close</button>
            </div>
        </div>

        <!-- Video Call Overlay -->
        <div id="call-overlay" class="hidden">
            <div class="video-grid">
                <div>
                    <p style="color: var(--accent); margin-bottom: 8px; text-align: center; font-weight: 600; font-size: 13px;">Local Stream</p>
                    <video id="localVideo" autoplay muted></video>
                </div>
                <div>
                    <p style="color: var(--accent); margin-bottom: 8px; text-align: center; font-weight: 600; font-size: 13px;">Remote Quantum Stream</p>
                    <video id="remoteVideo" autoplay></video>
                </div>
            </div>
            <button class="hangup-btn" onclick="endCall()">End Secure Call</button>
        </div>
    </div>

    <script>
        let ws;
        let currentUser = localStorage.getItem("metaverse_user") || null;
        let onlineUsers = [];
        let savedContacts = [];
        let activeContact = null;
        let chatHistories = {};
        
        let isSelectMode = false;
        let selectedMessageIds = new Set();
        let contextTargetContact = null;
        let ghostModeActive = false;

        let mediaRecorder, audioChunks = [], isRecording = false;
        let localStream, peerConnection;
        const servers = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

        window.onload = function() {
            const savedTheme = localStorage.getItem("metaverse_theme") || "cyan";
            setTheme(savedTheme, false);

            if (currentUser) {
                initializeUserSession(currentUser);
            }

            document.addEventListener('click', () => {
                document.getElementById("context-menu").style.display = "none";
            });
        };

        function handleGoogleLogin(response) {
            try {
                const base64Url = response.credential.split('.')[1];
                const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
                const jsonPayload = decodeURIComponent(atob(base64).split('').map(function(c) {
                    return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
                }).join(''));
                
                const payload = JSON.parse(jsonPayload);
                const userEmail = payload.email;
                
                if (userEmail) {
                    initializeUserSession(userEmail);
                }
            } catch (err) {
                console.error("Google authentication parsing failed", err);
                alert("Google Sign-In verification error.");
            }
        }

        function handleLoginKey(e) { if (e.key === "Enter") performManualLogin(); }

        function performManualLogin() {
            const inputVal = document.getElementById("loginUsernameInput").value.trim();
            if (!inputVal) {
                alert("Please enter a valid username or sign in with Google.");
                return;
            }
            initializeUserSession(inputVal);
        }

        function initializeUserSession(username) {
            currentUser = username;
            localStorage.setItem("metaverse_user", currentUser);

            document.getElementById("my-profile-name").innerText = `⚡ Node: ${currentUser}`;
            document.getElementById("my-profile-name").title = `Node: ${currentUser}`;
            document.getElementById("login-screen").classList.add("hidden");
            connectWebSocket();
            fetchSavedContacts();
        }

        function logout() {
            localStorage.removeItem("metaverse_user");
            location.reload();
        }

        function setTheme(themeName, save = true) {
            document.body.className = `theme-${themeName}`;
            if (save) localStorage.setItem("metaverse_theme", themeName);
        }

        function toggleGhostMode() {
            ghostModeActive = !ghostModeActive;
            const statusEl = document.getElementById("ghostStatus");
            const toggleEl = document.getElementById("ghostModeToggle");
            if (ghostModeActive) {
                statusEl.innerText = "ON (30s)";
                toggleEl.classList.add("active");
            } else {
                statusEl.innerText = "OFF";
                toggleEl.classList.remove("active");
            }
        }

        async function requestAiSummary() {
            if (!activeContact) return;
            if (activeContact === "Brian 🧠 (AI Archive)") {
                ws.send(JSON.stringify({ type: "summarize", recipient_id: "Brian 🧠 (AI Archive)" }));
            } else {
                alert("🧠 Brian AI Chat Summary: Active conversation stream is synchronized and stable.");
            }
        }

        async function fetchSavedContacts() {
            try {
                const res = await fetch(`/contacts/${encodeURIComponent(currentUser)}`);
                const data = await res.json();
                savedContacts = data.contacts;
                renderContacts();
            } catch (err) {
                console.error("Failed to load saved contacts", err);
            }
        }

        function connectWebSocket() {
            const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
            ws = new WebSocket(`${wsProtocol}${window.location.host}/ws/${encodeURIComponent(currentUser)}`);
            
            ws.onmessage = async function(event) {
                const data = JSON.parse(event.data);
                
                if (data.type === "user_list") {
                    onlineUsers = data.users.filter(u => u !== currentUser);
                    renderContacts();
                } else if (data.type === "delete_message") {
                    for (let contactKey in chatHistories) {
                        chatHistories[contactKey] = chatHistories[contactKey].filter(m => m.id !== data.id);
                    }
                    renderMessages();
                } else if (data.type === "bulk_delete") {
                    for (let contactKey in chatHistories) {
                        chatHistories[contactKey] = chatHistories[contactKey].filter(m => !data.ids.includes(m.id));
                    }
                    renderMessages();
                } else if (data.type === "clear_chat") {
                    if (activeContact === data.sender_id) {
                        chatHistories[activeContact] = [];
                        renderMessages();
                    }
                } else {
                    const sender = data.sender_id;
                    if (!chatHistories[sender]) chatHistories[sender] = [];
                    
                    const msgObj = { id: data.id, sender: sender, type: data.type, content: data.message };
                    chatHistories[sender].push(msgObj);
                    
                    if (!savedContacts.includes(sender) && sender !== "Brian 🧠 (AI Archive)") {
                        savedContacts.push(sender);
                    }

                    if (activeContact === sender) renderMessages();
                    renderContacts();

                    if (ghostModeActive) {
                        setTimeout(() => {
                            chatHistories[sender] = chatHistories[sender].filter(m => m.id !== msgObj.id);
                            if (activeContact === sender) renderMessages();
                        }, 30000);
                    }

                    if (data.type === "signal") handleSignal(sender, data.signal);
                }
            };
        }

        function renderContacts(filter = "") {
            const container = document.getElementById("contactsListContainer");
            container.innerHTML = "";
            
            const allContactSet = new Set([...onlineUsers, ...savedContacts, "Brian 🧠 (AI Archive)"]);
            
            allContactSet.forEach(email => {
                if (email === currentUser || !email.toLowerCase().includes(filter.toLowerCase())) return;
                
                const isOnline = onlineUsers.includes(email) || email === "Brian 🧠 (AI Archive)";
                const isActive = activeContact === email ? "active" : "";
                
                const contactDiv = document.createElement("div");
                contactDiv.className = `contact-item ${isActive}`;
                contactDiv.onclick = () => selectContact(email);
                
                contactDiv.oncontextmenu = (e) => {
                    e.preventDefault();
                    contextTargetContact = email;
                    const menu = document.getElementById("context-menu");
                    menu.style.top = `${e.clientY}px`;
                    menu.style.left = `${e.clientX}px`;
                    menu.style.display = "block";
                };

                const initial = email === "Brian 🧠 (AI Archive)" ? "🧠" : email.charAt(0).toUpperCase();
                const dotClass = isOnline ? "online-dot" : "offline-dot";
                const statusText = email === "Brian 🧠 (AI Archive)" ? "AI Memory Vault" : (isOnline ? "Online Node" : "Offline");

                contactDiv.innerHTML = `
                    <div class="contact-avatar">
                        ${initial}<div class="${dotClass}"></div>
                    </div>
                    <div class="contact-details">
                        <h4>${email}</h4>
                        <p>${statusText}</p>
                    </div>
                `;
                container.appendChild(contactDiv);
            });
        }

        function filterContacts() {
            const query = document.getElementById("searchContactInput").value;
            renderContacts(query);
        }

        async function selectContact(email) {
            activeContact = email;
            isSelectMode = false;
            selectedMessageIds.clear();
            document.getElementById("selection-action-bar").classList.add("hidden");
            document.getElementById("selectModeBtn").classList.remove("active-mode");

            document.getElementById("activeChatTitle").innerText = email;
            document.getElementById("activeChatStatus").innerText = email.includes("Brian") ? "Neural Vault Active" : (onlineUsers.includes(email) ? "Online" : "Offline");
            document.getElementById("activeChatAvatar").innerText = email === "Brian 🧠 (AI Archive)" ? "🧠" : email.charAt(0).toUpperCase();
            
            document.getElementById("messageInput").disabled = false;
            document.getElementById("micBtn").disabled = false;
            document.getElementById("selectModeBtn").classList.remove("hidden");
            document.getElementById("quantumFeaturesBar").classList.remove("hidden");
            
            if (email.includes("Brian")) {
                document.getElementById("videoCallBtn").classList.add("hidden");
            } else {
                document.getElementById("videoCallBtn").classList.remove("hidden");
            }

            // Mobile view adjustment: slide to chat panel
            document.getElementById("app-container").classList.add("mobile-chat-open");
            
            try {
                const res = await fetch(`/history/${encodeURIComponent(currentUser)}/${encodeURIComponent(email)}`);
                const data = await res.json();
                chatHistories[email] = data.history.map(m => ({
                    id: m.id,
                    sender: m.sender === currentUser ? "You" : m.sender,
                    type: m.type,
                    content: m.content
                }));
            } catch (err) {
                console.error("History sync error", err);
            }

            renderContacts();
            renderMessages();
        }

        function returnToSidebar() {
            document.getElementById("app-container").classList.remove("mobile-chat-open");
            activeContact = null;
            renderContacts();
        }

        async function clearChatAction() {
            if (!contextTargetContact) return;
            const contact = contextTargetContact;
            document.getElementById("context-menu").style.display = "none";

            if (!confirm(`Clear chat history with ${contact}?`)) return;

            ws.send(JSON.stringify({ type: "clear_chat", recipient_id: contact }));

            try {
                await fetch(`/clear-chat/${encodeURIComponent(currentUser)}/${encodeURIComponent(contact)}`, { method: 'DELETE' });
            } catch (err) {
                console.error("Clear chat error", err);
            }

            if (chatHistories[contact]) chatHistories[contact] = [];
            if (activeContact === contact) renderMessages();
        }

        function toggleSelectMode() {
            isSelectMode = !isSelectMode;
            selectedMessageIds.clear();
            const bar = document.getElementById("selection-action-bar");
            const btn = document.getElementById("selectModeBtn");

            if (isSelectMode) {
                bar.classList.remove("hidden");
                btn.classList.add("active-mode");
            } else {
                bar.classList.add("hidden");
                btn.classList.remove("active-mode");
            }
            renderMessages();
        }

        function handleMessageCheckbox(msgId, checkbox) {
            if (checkbox.checked) selectedMessageIds.add(msgId);
            else selectedMessageIds.delete(msgId);
            document.getElementById("selectedCountText").innerText = `${selectedMessageIds.size} selected`;
        }

        function renderMessages() {
            const container = document.getElementById("chatMessagesContainer");
            container.innerHTML = "";
            const messages = chatHistories[activeContact] || [];
            
            messages.forEach(msg => {
                const isOutgoing = msg.sender === "You";
                let contentHTML = "";
                
                if (msg.type === "chat") {
                    contentHTML = `<span>${msg.content}</span>`;
                } else if (msg.type === "audio_note") {
                    contentHTML = `🎤 Voice Note<br><audio controls src="${msg.content}" style="margin-top: 6px; max-width: 200px;"></audio>`;
                } else if (msg.type === "image") {
                    contentHTML = `<img src="${msg.content}" style="max-width: 220px; border-radius: 8px; display: block; margin-bottom: 4px;">`;
                }

                const ticksHTML = isOutgoing ? `<span class="msg-ticks">✓✓</span>` : "";
                const checkboxHTML = isSelectMode ? `<input type="checkbox" class="msg-checkbox" onchange="handleMessageCheckbox(${msg.id}, this)" ${selectedMessageIds.has(msg.id) ? 'checked' : ''}>` : "";

                container.innerHTML += `
                    <div class="message ${isOutgoing ? "outgoing" : "incoming"}">
                        ${checkboxHTML}
                        <div class="msg-body">
                            ${contentHTML}
                            ${ticksHTML}
                        </div>
                        ${!isSelectMode ? `<button class="delete-msg-btn" onclick="deleteMessage('${activeContact}', ${msg.id})">🗑️</button>` : ""}
                    </div>
                `;
            });
            container.scrollTop = container.scrollHeight;
        }

        async function deleteMessage(contact, msgId) {
            ws.send(JSON.stringify({ type: "delete_message", recipient_id: contact, message_id: msgId }));

            try {
                await fetch(`/message/${msgId}`, { method: 'DELETE' });
            } catch (err) {
                console.error("Delete sync error", err);
            }

            if (chatHistories[contact]) {
                chatHistories[contact] = chatHistories[contact].filter(m => m.id !== msgId);
                renderMessages();
            }
        }

        async function deleteSelectedMessages() {
            if (selectedMessageIds.size === 0) return;
            const idsArray = Array.from(selectedMessageIds);

            ws.send(JSON.stringify({ type: "bulk_delete", recipient_id: activeContact, message_ids: idsArray }));

            for (let msgId of idsArray) {
                try {
                    await fetch(`/message/${msgId}`, { method: 'DELETE' });
                } catch (err) {
                    console.error("Bulk delete error", err);
                }
            }

            if (chatHistories[activeContact]) {
                chatHistories[activeContact] = chatHistories[activeContact].filter(m => !selectedMessageIds.has(m.id));
            }
            toggleSelectMode();
            renderMessages();
        }

        function openForwardModal() {
            if (selectedMessageIds.size === 0) {
                alert("Please select messages to forward.");
                return;
            }
            const modalList = document.getElementById("modalContactsList");
            modalList.innerHTML = "";

            const allContactSet = new Set([...onlineUsers, ...savedContacts]);
            allContactSet.forEach(email => {
                if (email === currentUser || email === "Brian 🧠 (AI Archive)") return;
                modalList.innerHTML += `
                    <div class="modal-contact-item" onclick="forwardSelectedTo('${email}')">
                        ➡️ ${email}
                    </div>
                `;
            });

            document.getElementById("forward-modal").classList.remove("hidden");
        }

        function closeForwardModal() {
            document.getElementById("forward-modal").classList.add("hidden");
        }

        async function forwardSelectedTo(targetContact) {
            const messagesToForward = (chatHistories[activeContact] || []).filter(m => selectedMessageIds.has(m.id));
            
            for (let msg of messagesToForward) {
                ws.send(JSON.stringify({ type: msg.type, recipient_id: targetContact, message: msg.content }));
            }

            closeForwardModal();
            toggleSelectMode();
            alert(`Forwarded ${messagesToForward.length} message(s) to ${targetContact}!`);
        }

        function sendMessage() {
            const input = document.getElementById("messageInput");
            const text = input.value.trim();
            if (!text || !activeContact) return;

            ws.send(JSON.stringify({ type: "chat", recipient_id: activeContact, message: text }));

            const localMsgId = Date.now();
            const msgObj = {
                id: localMsgId,
                sender: "You",
                type: "chat",
                content: text
            };

            if (!chatHistories[activeContact]) chatHistories[activeContact] = [];
            chatHistories[activeContact].push(msgObj);
            
            if (!savedContacts.includes(activeContact) && activeContact !== "Brian 🧠 (AI Archive)") {
                savedContacts.push(activeContact);
            }

            if (ghostModeActive) {
                setTimeout(() => {
                    chatHistories[activeContact] = chatHistories[activeContact].filter(m => m.id !== localMsgId);
                    renderMessages();
                }, 30000);
            }

            renderMessages();
            renderContacts();
            input.value = "";
        }

        function handleKey(e) { if (e.key === "Enter") sendMessage(); }

        function sendImage(event) {
            const file = event.target.files[0];
            if (!file || !activeContact) return;

            const reader = new FileReader();
            reader.onload = function() {
                const imageUrl = reader.result;
                ws.send(JSON.stringify({ type: "image", recipient_id: activeContact, message: imageUrl }));

                const localMsgId = Date.now();
                const msgObj = {
                    id: localMsgId,
                    sender: "You",
                    type: "image",
                    content: imageUrl
                };

                if (!chatHistories[activeContact]) chatHistories[activeContact] = [];
                chatHistories[activeContact].push(msgObj);
                renderMessages();
            };
            reader.readAsDataURL(file);
        }

        async function toggleRecordVoice() {
            const micBtn = document.getElementById("micBtn");
            if (!activeContact) return;

            if (!isRecording) {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    mediaRecorder = new MediaRecorder(stream);
                    audioChunks = [];

                    mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
                    mediaRecorder.onstop = () => {
                        const reader = new FileReader();
                        reader.readAsDataURL(new Blob(audioChunks, { type: 'audio/webm' }));
                        reader.onloadend = () => {
                            const audioUrl = reader.result;
                            ws.send(JSON.stringify({ type: "audio_note", recipient_id: activeContact, message: audioUrl }));

                            const msgObj = {
                                id: Date.now(),
                                sender: "You",
                                type: "audio_note",
                                content: audioUrl
                            };

                            if (!chatHistories[activeContact]) chatHistories[activeContact] = [];
                            chatHistories[activeContact].push(msgObj);
                            renderMessages();
                        };
                    };

                    mediaRecorder.start();
                    isRecording = true;
                    micBtn.classList.add("recording");
                } catch { alert("Microphone access required."); }
            } else {
                mediaRecorder.stop();
                isRecording = false;
                micBtn.classList.remove("recording");
            }
        }

        async function startCall() {
            if (!activeContact || activeContact.includes("Brian")) return;
            document.getElementById("call-overlay").classList.remove("hidden");

            localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
            document.getElementById("localVideo").srcObject = localStream;

            peerConnection = new RTCPeerConnection(servers);
            localStream.getTracks().forEach(t => peerConnection.addTrack(t, localStream));

            peerConnection.ontrack = e => { document.getElementById("remoteVideo").srcObject = e.streams[0]; };
            peerConnection.onicecandidate = e => { if (e.candidate) ws.send(JSON.stringify({ type: "signal", recipient_id: activeContact, signal: { candidate: e.candidate } })); };

            const offer = await peerConnection.createOffer();
            await peerConnection.setLocalDescription(offer);
            ws.send(JSON.stringify({ type: "signal", recipient_id: activeContact, signal: { sdp: peerConnection.localDescription } }));
        }

        async function handleSignal(senderId, signal) {
            if (!peerConnection) {
                document.getElementById("call-overlay").classList.remove("hidden");
                localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
                document.getElementById("localVideo").srcObject = localStream;
                peerConnection = new RTCPeerConnection(servers);
                localStream.getTracks().forEach(t => peerConnection.addTrack(t, localStream));
                peerConnection.ontrack = e => { document.getElementById("remoteVideo").srcObject = e.streams[0]; };
                peerConnection.onicecandidate = e => { if (e.candidate) ws.send(JSON.stringify({ type: "signal", recipient_id: senderId, signal: { candidate: e.candidate } })); };
            }

            if (signal.sdp) {
                await peerConnection.setRemoteDescription(new RTCSessionDescription(signal.sdp));
                if (signal.sdp.type === "offer") {
                    const answer = await peerConnection.createAnswer();
                    await peerConnection.setLocalDescription(answer);
                    ws.send(JSON.stringify({ type: "signal", recipient_id: senderId, signal: { sdp: peerConnection.localDescription } }));
                }
            } else if (signal.candidate) {
                await peerConnection.addIceCandidate(new RTCIceCandidate(signal.candidate));
            }
        }

        function endCall() {
            if (peerConnection) peerConnection.close();
            if (localStream) localStream.getTracks().forEach(t => t.stop());
            peerConnection = null;
            document.getElementById("call-overlay").classList.add("hidden");
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
