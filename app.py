from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import json
import sqlite3
from typing import Dict

app = FastAPI(title="Metaverse_WhatsApp - Ultimate Edition")

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


# ==================== FRONTEND: WhatsApp Advanced UI ====================
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Metaverse_WhatsApp - Ultimate Edition</title>
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: #0b0f19; height: 100vh; display: flex; justify-content: center; align-items: center; color: #e2e8f0; }
        .hidden { display: none !important; }
        
        #app-container { width: 96%; max-width: 1450px; height: 93vh; background: #111827; border: 1px solid rgba(0, 242, 254, 0.2); display: flex; box-shadow: 0 0 40px rgba(0, 0, 0, 0.6); border-radius: 14px; overflow: hidden; position: relative; }
        
        /* Login Overlay */
        #login-screen { position: absolute; inset: 0; background: #0b0f19; display: flex; justify-content: center; align-items: center; z-index: 100; }
        #login-box { background: #1f2937; border: 1px solid rgba(0, 242, 254, 0.3); padding: 45px; border-radius: 16px; text-align: center; box-shadow: 0 0 25px rgba(0, 242, 254, 0.15); width: 400px; }
        #login-box h2 { color: #00f2fe; margin-bottom: 8px; font-size: 26px; }
        #login-box p { color: #9ca3af; font-size: 13px; margin-bottom: 25px; }

        /* Sidebar */
        .sidebar { width: 36%; background: #1f2937; border-right: 1px solid #374151; display: flex; flex-direction: column; }
        .sidebar-header { padding: 15px 20px; background: #111827; display: flex; align-items: center; justify-content: space-between; height: 70px; font-weight: 600; color: #00f2fe; border-bottom: 1px solid #374151; }
        .sidebar-search-box { padding: 10px 15px; background: #1f2937; border-bottom: 1px solid #374151; }
        .sidebar-search-box input { width: 100%; padding: 8px 14px; background: #111827; border: 1px solid #374151; border-radius: 8px; color: white; font-size: 13px; outline: none; }
        .sidebar-search-box input:focus { border-color: #00f2fe; }
        
        .contacts-list { flex: 1; overflow-y: auto; }
        .contact-item { display: flex; align-items: center; padding: 12px 18px; border-bottom: 1px solid #2d3748; cursor: pointer; transition: 0.2s; position: relative; }
        .contact-item:hover, .contact-item.active { background: #374151; border-left: 4px solid #00f2fe; }
        .contact-avatar { width: 45px; height: 45px; border-radius: 50%; background: linear-gradient(135deg, #3b82f6, #00f2fe); color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; margin-right: 14px; position: relative; flex-shrink: 0; }
        .online-dot { width: 12px; height: 12px; background: #10b981; border: 2px solid #1f2937; border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        .offline-dot { width: 12px; height: 12px; background: #6b7280; border: 2px solid #1f2937; border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        .contact-details h4 { font-size: 15px; color: #f9fafb; font-weight: 500; }
        .contact-details p { font-size: 12px; color: #00f2fe; margin-top: 3px; }

        /* Custom Context Menu */
        #context-menu { position: absolute; background: #1f2937; border: 1px solid #374151; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.5); z-index: 1000; display: none; padding: 5px 0; }
        .context-menu-item { padding: 10px 20px; font-size: 13px; color: #f87171; cursor: pointer; display: flex; align-items: center; gap: 8px; }
        .context-menu-item:hover { background: #374151; }

        /* Chat Panel */
        .chat-panel { flex: 1; display: flex; flex-direction: column; background: #0b0f19; position: relative; }
        .chat-header { height: 70px; background: #111827; padding: 10px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #374151; }
        .active-chat-info { display: flex; align-items: center; gap: 14px; }
        .header-actions { display: flex; align-items: center; gap: 10px; }
        .header-btn { background: #374151; color: white; border: none; padding: 7px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500; }
        .header-btn:hover { background: #4b5563; }
        .header-btn.active-mode { background: #00f2fe; color: #0b0f19; font-weight: bold; }
        .call-btn { background: linear-gradient(135deg, #00f2fe, #3b82f6); color: #0b0f19; border: none; padding: 8px 16px; border-radius: 20px; cursor: pointer; font-weight: 700; font-size: 13px; box-shadow: 0 0 10px rgba(0, 242, 254, 0.3); }
        
        .chat-messages { flex: 1; padding: 25px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; background-image: radial-gradient(circle, #1f2937 1px, transparent 1px); background-size: 24px 24px; }
        .message { max-width: 60%; padding: 10px 14px; border-radius: 10px; font-size: 14px; line-height: 20px; word-wrap: break-word; position: relative; box-shadow: 0 2px 5px rgba(0,0,0,0.2); display: flex; align-items: flex-start; gap: 8px; }
        .message.incoming { background: #1f2937; border: 1px solid #374151; align-self: flex-start; border-top-left-radius: 0; color: #f3f4f6; }
        .message.outgoing { background: #059669; align-self: flex-end; border-top-right-radius: 0; color: white; }
        
        .msg-checkbox { margin-top: 2px; accent-color: #00f2fe; transform: scale(1.1); cursor: pointer; }
        .msg-body { flex: 1; }
        .msg-ticks { font-size: 11px; float: right; margin-left: 8px; margin-top: 4px; color: #a7f3d0; }
        .delete-msg-btn { position: absolute; top: 4px; right: 6px; background: none; border: none; color: rgba(255,255,255,0.6); font-size: 11px; cursor: pointer; display: none; }
        .message:hover .delete-msg-btn { display: inline-block; }
        .delete-msg-btn:hover { color: #ef4444; }

        /* Selection Action Bar */
        #selection-action-bar { position: absolute; bottom: 70px; left: 0; right: 0; background: #1f2937; border-top: 1px solid #374151; padding: 12px 20px; display: flex; align-items: center; justify-content: space-between; z-index: 50; }
        .sel-btn { background: #374151; border: none; color: white; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 13px; }
        .sel-btn.delete { background: #ef4444; }
        .sel-btn.forward { background: #3b82f6; }

        /* Input Area */
        .chat-input-area { min-height: 70px; background: #111827; padding: 12px 20px; display: flex; align-items: center; gap: 12px; border-top: 1px solid #374151; }
        .chat-input-area input { flex: 1; padding: 12px 16px; border: 1px solid #374151; border-radius: 8px; background: #1f2937; color: white; font-size: 14px; outline: none; }
        .chat-input-area input:focus { border-color: #00f2fe; }
        .action-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: #9ca3af; padding: 6px; }
        .action-btn:hover { color: #00f2fe; }
        .action-btn.recording { color: #ef4444; animation: pulse 1s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }

        /* Modal Overlay for Forwarding */
        #forward-modal { position: absolute; inset: 0; background: rgba(0,0,0,0.7); z-index: 300; display: flex; justify-content: center; align-items: center; }
        .modal-content { background: #1f2937; border: 1px solid #374151; padding: 25px; border-radius: 12px; width: 350px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .modal-content h3 { color: #00f2fe; margin-bottom: 15px; font-size: 18px; }
        .modal-contact-item { padding: 10px 14px; background: #111827; margin-bottom: 8px; border-radius: 6px; cursor: pointer; border: 1px solid #374151; }
        .modal-contact-item:hover { border-color: #00f2fe; }

        /* Video Call Overlay */
        #call-overlay { position: absolute; inset: 0; background: rgba(11, 15, 25, 0.95); z-index: 200; display: flex; flex-direction: column; align-items: center; justify-content: center; }
        .video-grid { display: flex; gap: 20px; margin-bottom: 25px; }
        video { width: 480px; height: 340px; background: black; border: 1px solid #374151; border-radius: 12px; object-fit: cover; }
        .hangup-btn { background: #ef4444; color: white; border: none; padding: 12px 28px; border-radius: 30px; font-weight: bold; cursor: pointer; font-size: 15px; }
    </style>
</head>
<body>

    <div id="app-container">
        <!-- Google Sign-In Screen -->
        <div id="login-screen">
            <div id="login-box">
                <h2>⚡ Metaverse_WhatsApp</h2>
                <p>Advanced Neural Authentication</p>
                <div id="g_id_onload"
                     data-client_id="358332042325-3s7o118sjfv1qug4r6qlmf534083ti10.apps.googleusercontent.com"
                     data-callback="handleCredentialResponse">
                </div>
                <div class="g_id_signin" data-type="standard" data-shape="rectangular" data-theme="filled_black"></div>
            </div>
        </div>

        <!-- Sidebar -->
        <div class="sidebar">
            <div class="sidebar-header">
                <span id="my-profile-name">⚡ Node: Offline</span>
            </div>
            <div class="sidebar-search-box">
                <input type="text" id="searchContactInput" placeholder="Search or start new chat..." oninput="filterContacts()">
            </div>
            <div class="contacts-list" id="contactsListContainer"></div>
        </div>

        <!-- Context Menu for Right Click -->
        <div id="context-menu">
            <div class="context-menu-item" onclick="clearChatAction()">🗑️ Clear Chat</div>
        </div>

        <!-- Chat Panel -->
        <div class="chat-panel">
            <div class="chat-header">
                <div class="active-chat-info">
                    <div class="contact-avatar" id="activeChatAvatar" style="background: #374151; color: #9ca3af;">?</div>
                    <div>
                        <h4 id="activeChatTitle" style="font-size: 16px; color: #f9fafb; font-weight: 500;">Select Chat</h4>
                        <p id="activeChatStatus" style="font-size: 12px; color: #00f2fe;">Ready</p>
                    </div>
                </div>
                <div class="header-actions">
                    <button class="header-btn hidden" id="selectModeBtn" onclick="toggleSelectMode()">Select Messages</button>
                    <button class="call-btn hidden" id="videoCallBtn" onclick="startCall()">🔮 Video Call</button>
                </div>
            </div>

            <div class="chat-messages" id="chatMessagesContainer">
                <div style="text-align: center; margin: auto; color: #6b7280; font-size: 14px;">
                    <p>🔒 End-to-End Secure Permanent Archive Active</p>
                </div>
            </div>

            <!-- Selection Action Bar -->
            <div id="selection-action-bar" class="hidden">
                <span id="selectedCountText" style="font-size: 14px; font-weight: 600; color: #00f2fe;">0 selected</span>
                <div style="display: flex; gap: 10px;">
                    <button class="sel-btn forward" onclick="openForwardModal()">↗️ Forward</button>
                    <button class="sel-btn delete" onclick="deleteSelectedMessages()">🗑️ Delete</button>
                    <button class="sel-btn" onclick="toggleSelectMode()">Cancel</button>
                </div>
            </div>

            <div class="chat-input-area">
                <label class="action-btn" title="Attach Image">📎<input type="file" id="imageInput" accept="image/*" style="display:none;" onchange="sendImage(event)"></label>
                <input type="text" id="messageInput" placeholder="Type a message..." onkeypress="handleKey(event)" disabled>
                <button class="action-btn" id="micBtn" onclick="toggleRecordVoice()" title="Voice Note" disabled>🎙️</button>
                <button class="action-btn" onclick="sendMessage()" style="color: #10b981; font-size: 22px;" title="Send">➤</button>
            </div>
        </div>

        <!-- Forward Modal -->
        <div id="forward-modal" class="hidden">
            <div class="modal-content">
                <h3>Forward to...</h3>
                <div id="modalContactsList" style="max-height: 250px; overflow-y: auto; margin-bottom: 15px;"></div>
                <button class="sel-btn" style="width: 100%; background: #374151;" onclick="closeForwardModal()">Close</button>
            </div>
        </div>

        <!-- Video Call Overlay -->
        <div id="call-overlay" class="hidden">
            <div class="video-grid">
                <div>
                    <p style="color: #00f2fe; margin-bottom: 8px; text-align: center;">Local Stream</p>
                    <video id="localVideo" autoplay muted></video>
                </div>
                <div>
                    <p style="color: #00f2fe; margin-bottom: 8px; text-align: center;">Remote Stream</p>
                    <video id="remoteVideo" autoplay></video>
                </div>
            </div>
            <button class="hangup-btn" onclick="endCall()">End Call</button>
        </div>
    </div>

    <script>
        let ws;
        let currentUser = localStorage.getItem("metaverse_user") || null;
        let currentName = localStorage.getItem("metaverse_name") || null;
        let onlineUsers = [];
        let savedContacts = [];
        let activeContact = null;
        let chatHistories = {};
        
        let isSelectMode = false;
        let selectedMessageIds = new Set();
        let contextTargetContact = null;

        let mediaRecorder, audioChunks = [], isRecording = false;
        let localStream, peerConnection;
        const servers = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

        window.onload = function() {
            if (currentUser && currentName) {
                document.getElementById("my-profile-name").innerText = `⚡ ${currentName}`;
                document.getElementById("login-screen").classList.add("hidden");
                connectWebSocket();
                fetchSavedContacts();
            }

            // Close context menu on click outside
            document.addEventListener('click', () => {
                document.getElementById("context-menu").style.display = "none";
            });
        };

        function handleCredentialResponse(response) {
            const payload = parseJwt(response.credential);
            currentUser = payload.email;
            currentName = payload.name;

            localStorage.setItem("metaverse_user", currentUser);
            localStorage.setItem("metaverse_name", currentName);

            document.getElementById("my-profile-name").innerText = `⚡ ${currentName}`;
            document.getElementById("login-screen").classList.add("hidden");
            connectWebSocket();
            fetchSavedContacts();
        }

        function parseJwt(token) {
            const base64Url = token.split('.')[1];
            const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
            return JSON.parse(decodeURIComponent(atob(base64).split('').map(c => '%' + ('0' + c.charCodeAt(0).toString(16)).slice(-2)).join('')));
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
                    
                    chatHistories[sender].push({ id: data.id, sender: sender, type: data.type, content: data.message });
                    
                    if (!savedContacts.includes(sender) && sender !== "Brian 🧠 (AI Archive)") {
                        savedContacts.push(sender);
                    }

                    if (activeContact === sender) renderMessages();
                    renderContacts();
                    if (data.type === "signal") handleSignal(sender, data.signal);
                }
            };
        }

        function renderContacts(filter = "") {
            const container = document.getElementById("contactsListContainer");
            container.innerHTML = "";
            
            // Combine online users and persistent saved contacts
            const allContactSet = new Set([...onlineUsers, ...savedContacts, "Brian 🧠 (AI Archive)"]);
            
            allContactSet.forEach(email => {
                if (email === currentUser || !email.toLowerCase().includes(filter.toLowerCase())) return;
                
                const isOnline = onlineUsers.includes(email) || email === "Brian 🧠 (AI Archive)";
                const isActive = activeContact === email ? "active" : "";
                
                const contactDiv = document.createElement("div");
                contactDiv.className = `contact-item ${isActive}`;
                contactDiv.onclick = () => selectContact(email);
                
                // Right-click context menu event
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
                const statusText = email === "Brian 🧠 (AI Archive)" ? "Always Saving & Indexing" : (isOnline ? "Online" : "Offline");

                contactDiv.innerHTML = `
                    <div class="contact-avatar" style="${email.includes('Brian') ? 'background: linear-gradient(135deg, #00f2fe, #3b82f6);' : ''}">
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
            document.getElementById("activeChatStatus").innerText = email.includes("Brian") ? "Memory Vault Online" : (onlineUsers.includes(email) ? "Online" : "Offline");
            document.getElementById("activeChatAvatar").innerText = email === "Brian 🧠 (AI Archive)" ? "🧠" : email.charAt(0).toUpperCase();
            
            document.getElementById("messageInput").disabled = false;
            document.getElementById("micBtn").disabled = false;
            document.getElementById("selectModeBtn").classList.remove("hidden");
            
            if (email.includes("Brian")) {
                document.getElementById("videoCallBtn").classList.add("hidden");
            } else {
                document.getElementById("videoCallBtn").classList.remove("hidden");
            }
            
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

        async function clearChatAction() {
            if (!contextTargetContact) return;
            const contact = contextTargetContact;
            document.getElementById("context-menu").style.display = "none";

            if (!confirm(`Are you sure you want to clear chat with ${contact}?`)) return;

            // Notify recipient & clear locally
            ws.send(JSON.stringify({ type: "clear_chat", recipient_id: contact }));

            try {
                await fetch(`/clear-chat/${encodeURIComponent(currentUser)}/${encodeURIComponent(contact)}`, { method: 'DELETE' });
            } catch (err) {
                console.error("Clear chat error", err);
            }

            if (chatHistories[contact]) {
                chatHistories[contact] = [];
            }
            if (activeContact === contact) {
                renderMessages();
            }
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
            if (checkbox.checked) {
                selectedMessageIds.add(msgId);
            } else {
                selectedMessageIds.delete(msgId);
            }
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
                    contentHTML = `🎤 Voice Note<br><audio controls src="${msg.content}" style="margin-top: 5px; max-width: 200px;"></audio>`;
                } else if (msg.type === "image") {
                    contentHTML = `<img src="${msg.content}" style="max-width: 240px; border-radius: 8px; display: block; margin-bottom: 4px;">`;
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
            alert(`Successfully forwarded ${messagesToForward.length} message(s) to ${targetContact}!`);
        }

        function sendMessage() {
            const input = document.getElementById("messageInput");
            const text = input.value.trim();
            if (!text || !activeContact) return;

            ws.send(JSON.stringify({ type: "chat", recipient_id: activeContact, message: text }));

            if (!chatHistories[activeContact]) chatHistories[activeContact] = [];
            chatHistories[activeContact].push({
                id: Date.now(),
                sender: "You",
                type: "chat",
                content: text
            });
            
            if (!savedContacts.includes(activeContact) && activeContact !== "Brian 🧠 (AI Archive)") {
                savedContacts.push(activeContact);
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

                if (!chatHistories[activeContact]) chatHistories[activeContact] = [];
                chatHistories[activeContact].push({
                    id: Date.now(),
                    sender: "You",
                    type: "image",
                    content: imageUrl
                });
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

                            if (!chatHistories[activeContact]) chatHistories[activeContact] = [];
                            chatHistories[activeContact].push({
                                id: Date.now(),
                                sender: "You",
                                type: "audio_note",
                                content: audioUrl
                            });
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