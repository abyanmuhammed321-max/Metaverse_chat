import sqlite3
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
import json
import os
import shutil

app = FastAPI()

DB_FILE = "whatsapp_clone.db"
UPLOAD_DIR = "static_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            phone TEXT PRIMARY KEY,
            username TEXT,
            about TEXT,
            avatar TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_phone TEXT,
            contact_phone TEXT,
            contact_name TEXT,
            UNIQUE(user_phone, contact_phone)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            recipient TEXT,
            content TEXT,
            msg_type TEXT DEFAULT 'text',
            file_url TEXT,
            reactions TEXT DEFAULT '{}',
            timestamp TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS statuses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT,
            content TEXT,
            media_url TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, phone: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[phone] = websocket

    def disconnect(self, phone: str):
        if phone in self.active_connections:
            del self.active_connections[phone]

    async def send_personal(self, message: dict, phone: str):
        if phone in self.active_connections:
            await self.active_connections[phone].send_text(json.dumps(message))

manager = ConnectionManager()

@app.get("/", response_class=HTMLResponse)
async def get_chat_app():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>WhatsApp Web Clone</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        :root {
            --bg-header: #00a884;
            --bg-app-top: #00a884;
            --bg-app-body: #efeae2;
            --panel-bg: #ffffff;
            --incoming-bg: #ffffff;
            --outgoing-bg: #d9fdd3;
            --text-primary: #111b21;
            --text-secondary: #667781;
            --border-color: #e9edef;
            --input-bg: #f0f2f5;
        }
        [data-theme="dark"] {
            --bg-header: #202c33;
            --bg-app-top: #111b21;
            --bg-app-body: #0b141a;
            --panel-bg: #111b21;
            --incoming-bg: #202c33;
            --outgoing-bg: #005c4b;
            --text-primary: #e9edef;
            --text-secondary: #8696a0;
            --border-color: #222d34;
            --input-bg: #2a3942;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Helvetica, Arial, sans-serif; }
        body { background-color: var(--bg-app-body); height: 100vh; display: flex; justify-content: center; align-items: center; overflow: hidden; }
        
        /* Containers */
        #auth-container, #app-container { width: 100vw; height: 100vh; display: flex; }
        #app-container { max-width: 1600px; max-height: 95vh; box-shadow: 0 6px 18px rgba(0,0,0,0.2); border-radius: 8px; overflow: hidden; }
        .hidden { display: none !important; }

        /* Auth Screen */
        #auth-container { background: var(--bg-app-body); justify-content: center; align-items: center; }
        .auth-card { background: var(--panel-bg); padding: 40px; border-radius: 12px; width: 400px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.1); color: var(--text-primary); }
        .auth-card h2 { margin-bottom: 20px; color: var(--bg-header); }
        .auth-card input { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid var(--border-color); border-radius: 6px; background: var(--input-bg); color: var(--text-primary); }
        .auth-card button { width: 100%; padding: 12px; background: #00a884; color: white; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; margin-top: 10px; }

        /* Left Sidebar */
        .sidebar { width: 35%; background: var(--panel-bg); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; }
        .sidebar-header { padding: 10px 16px; background: var(--bg-header); display: flex; justify-content: space-between; align-items: center; color: white; height: 60px; }
        .sidebar-header .icons i { margin-left: 20px; cursor: pointer; font-size: 18px; }
        
        .search-bar { padding: 8px 12px; background: var(--panel-bg); border-bottom: 1px solid var(--border-color); display: flex; align-items: center; }
        .search-bar input { width: 100%; padding: 8px 12px 8px 32px; border-radius: 8px; border: none; background: var(--input-bg); color: var(--text-primary); outline: none; }
        .search-wrapper { position: relative; width: 100%; }
        .search-wrapper i { position: absolute; left: 10px; top: 10px; color: var(--text-secondary); }

        .chat-list { flex: 1; overflow-y: auto; }
        .chat-item { display: flex; padding: 12px 16px; cursor: pointer; border-bottom: 1px solid var(--border-color); align-items: center; }
        .chat-item:hover, .chat-item.active { background: var(--input-bg); }
        .avatar { width: 45px; height: 45px; border-radius: 50%; background: #dfe5e7; display: flex; justify-content: center; align-items: center; font-weight: bold; color: #54656f; margin-right: 15px; flex-shrink: 0; }
        .chat-info { flex: 1; overflow: hidden; }
        .chat-info .top-row { display: flex; justify-content: space-between; margin-bottom: 4px; }
        .chat-info .name { font-weight: 600; color: var(--text-primary); }
        .chat-info .time { font-size: 12px; color: var(--text-secondary); }
        .chat-info .preview { font-size: 13px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

        /* Main Chat Area */
        .main-chat { flex: 65%; display: flex; flex-direction: column; background: var(--bg-app-body); }
        .chat-header { padding: 10px 16px; background: var(--panel-bg); display: flex; justify-content: space-between; align-items: center; height: 60px; border-left: 1px solid var(--border-color); }
        .chat-header-user { display: flex; align-items: center; }
        
        .messages-container { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; background-image: radial-gradient(#cbd5e1 1px, transparent 1px); background-size: 20px 20px; }
        [data-theme="dark"] .messages-container { background-image: radial-gradient(#1f2c34 1px, transparent 1px); }

        .message { max-width: 65%; padding: 8px 12px; border-radius: 8px; position: relative; font-size: 14px; word-wrap: break-word; color: var(--text-primary); box-shadow: 0 1px 0.5px rgba(0,0,0,0.13); }
        .message.incoming { background: var(--incoming-bg); align-self: flex-start; border-top-left-radius: 0; }
        .message.outgoing { background: var(--outgoing-bg); align-self: flex-end; border-top-right-radius: 0; }
        .message .meta { font-size: 10px; color: var(--text-secondary); float: right; margin-left: 10px; margin-top: 4px; display: flex; align-items: center; gap: 3px; }
        
        /* Chat Input Box */
        .chat-input-area { padding: 10px 16px; background: var(--panel-bg); display: flex; align-items: center; gap: 10px; height: 62px; }
        .chat-input-area i { font-size: 20px; color: var(--text-secondary); cursor: pointer; }
        .chat-input-area input { flex: 1; padding: 10px 14px; border-radius: 8px; border: none; background: var(--input-bg); color: var(--text-primary); outline: none; font-size: 15px; }

        /* Modal / Tabs for Status & Groups */
        .modal { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: flex; justify-content: center; align-items: center; z-index: 1000; }
        .modal-content { background: var(--panel-bg); padding: 30px; border-radius: 8px; width: 400px; color: var(--text-primary); }
        .modal-content input, .modal-content textarea { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid var(--border-color); border-radius: 6px; background: var(--input-bg); color: var(--text-primary); }
    </style>
</head>
<body data-theme="light">

    <!-- AUTH SCREEN -->
    <div id="auth-container">
        <div class="auth-card" id="phoneStep1">
            <h2>WhatsApp Sign In</h2>
            <p style="color: var(--text-secondary); font-size: 14px; margin-bottom: 15px;">Enter your mobile number to get started</p>
            <input type="text" id="loginPhoneInput" placeholder="Phone (e.g. +1234567890)">
            <button onclick="sendOtpCode()">Next</button>
        </div>
        <div class="auth-card hidden" id="phoneStep2">
            <h2>Verify OTP</h2>
            <p id="otpInfoText" style="color: var(--text-secondary); font-size: 14px; margin-bottom: 15px;"></p>
            <input type="text" id="otpCodeInput" placeholder="Enter 4-digit OTP">
            <button onclick="verifyOtpCode()">Verify & Login</button>
        </div>
        <div class="auth-card hidden" id="profileStep">
            <h2>Profile Info</h2>
            <input type="text" id="usernameInput" placeholder="Your Name">
            <input type="text" id="aboutInput" placeholder="About (e.g. Busy)">
            <button onclick="completeLogin()">Start Messaging</button>
        </div>
    </div>

    <!-- MAIN APP SCREEN -->
    <div id="app-container" class="hidden">
        <!-- SIDEBAR -->
        <div class="sidebar">
            <div class="sidebar-header">
                <div class="avatar" id="myAvatar" style="cursor: pointer;" onclick="openProfileSettings()">U</div>
                <div class="icons">
                    <i class="fa-solid fa-circle-notch" title="Status" onclick="openStatusModal()"></i>
                    <i class="fa-solid fa-users" title="New Group" onclick="openGroupModal()"></i>
                    <i class="fa-solid fa-message" title="New Chat" onclick="openNewContactModal()"></i>
                    <i class="fa-solid fa-moon" title="Toggle Theme" onclick="toggleTheme()"></i>
                </div>
            </div>
            <div class="search-bar">
                <div class="search-wrapper">
                    <i class="fa-solid fa-magnifying-glass"></i>
                    <input type="text" placeholder="Search or start new chat" id="chatSearch" onkeyup="filterChats()">
                </div>
            </div>
            <div class="chat-list" id="chatListContainer">
                <!-- Chats dynamically loaded -->
            </div>
        </div>

        <!-- MAIN CHAT -->
        <div class="main-chat">
            <div class="chat-header">
                <div class="chat-header-user">
                    <div class="avatar" id="activeChatAvatar">#</div>
                    <div>
                        <div class="name" id="activeChatName" style="font-weight: 600; color: var(--text-primary);">Select a chat</div>
                        <div class="preview" id="typingIndicator" style="font-size: 12px; color: var(--bg-header);"></div>
                    </div>
                </div>
                <div class="icons" style="display: flex; gap: 20px; color: var(--text-secondary);">
                    <i class="fa-solid fa-paperclip" onclick="triggerFileUpload()" title="Attach File"></i>
                    <i class="fa-solid fa-ellipsis-vertical"></i>
                </div>
            </div>

            <div class="messages-container" id="messagesContainer">
                <div style="margin: auto; text-align: center; color: var(--text-secondary);">
                    <i class="fa-brands fa-whatsapp" style="font-size: 64px; margin-bottom: 10px; color: #00a884;"></i>
                    <h3>WhatsApp Web Clone</h3>
                    <p>Send and receive messages securely with real-time sync.</p>
                </div>
            </div>

            <div class="chat-input-area" id="inputArea" style="display: none;">
                <i class="fa-regular fa-face-smile" onclick="toggleEmojiPicker()"></i>
                <input type="file" id="fileInput" style="display: none;" onchange="uploadFile(this)">
                <input type="text" id="messageInput" placeholder="Type a message" onkeypress="handleKeyPress(event)" oninput="sendTypingSignal()">
                <i class="fa-solid fa-microphone" onclick="sendVoiceNote()" title="Voice Note"></i>
                <i class="fa-solid fa-paper-plane" onclick="sendMessage()"></i>
            </div>
        </div>
    </div>

    <!-- MODALS -->
    <div id="contactModal" class="modal hidden">
        <div class="modal-content">
            <h3>Add Contact</h3>
            <input type="text" id="newContactPhone" placeholder="Contact Phone (+1234...)">
            <input type="text" id="newContactName" placeholder="Contact Name">
            <button onclick="addContact()">Add</button>
            <button onclick="closeModals()" style="background: #ea4335; margin-top: 5px;">Cancel</button>
        </div>
    </div>

    <div id="groupModal" class="modal hidden">
        <div class="modal-content">
            <h3>Create Group</h3>
            <input type="text" id="groupNameInput" placeholder="Group Subject">
            <button onclick="createGroup()">Create</button>
            <button onclick="closeModals()" style="background: #ea4335; margin-top: 5px;">Cancel</button>
        </div>
    </div>

    <div id="statusModal" class="modal hidden">
        <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
            <h3>Statuses (24h)</h3>
            <div id="statusFeed" style="margin: 15px 0;"></div>
            <textarea id="statusTextInput" placeholder="Type a status update..."></textarea>
            <button onclick="postStatus()">Post Status</button>
            <button onclick="closeModals()" style="background: #ea4335; margin-top: 5px;">Close</button>
        </div>
    </div>

    <script>
        let currentUser = null;
        let activeRecipient = null;
        let ws = null;
        let generatedOtpCode = "";
        let typingTimeout = null;

        function sendOtpCode() {
            const phone = document.getElementById("loginPhoneInput").value.trim();
            if (!phone) { alert("Enter a valid phone number"); return; }
            generatedOtpCode = Math.floor(1000 + Math.random() * 9000).toString();
            // FIXED: Proper JS property setting syntax below
            document.getElementById("otpInfoText").innerHTML = `📱 [Simulated SMS Sent]: Your OTP code is <b>${generatedOtpCode}</b>`;
            document.getElementById("phoneStep1").classList.add("hidden");
            document.getElementById("phoneStep2").classList.remove("hidden");
        }

        function verifyOtpCode() {
            const code = document.getElementById("otpCodeInput").value.trim();
            if (code === generatedOtpCode) {
                document.getElementById("phoneStep2").classList.add("hidden");
                document.getElementById("profileStep").classList.remove("hidden");
            } else {
                alert("Incorrect OTP code!");
            }
        }

        async function completeLogin() {
            const phone = document.getElementById("loginPhoneInput").value.trim();
            const username = document.getElementById("usernameInput").value.trim() || "WhatsApp User";
            const about = document.getElementById("aboutInput").value.trim() || "Available";

            const res = await fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ phone, username, about })
            });
            currentUser = await res.json();
            
            document.getElementById("auth-container").classList.add("hidden");
            document.getElementById("app-container").classList.remove("hidden");
            document.getElementById("myAvatar").innerText = currentUser.username.charAt(0).toUpperCase();

            initWebSocket();
            loadContacts();
        }

        function initWebSocket() {
            ws = new WebSocket(`ws://${window.location.host}/ws/${currentUser.phone}`);
            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                if (data.type === "message") {
                    if (activeRecipient === data.sender || activeRecipient === data.recipient) {
                        appendMessage(data);
                    }
                    loadContacts(); // update preview list
                } else if (data.type === "typing") {
                    if (activeRecipient === data.sender) {
                        document.getElementById("typingIndicator").innerText = "typing...";
                        clearTimeout(typingTimeout);
                        typingTimeout = setTimeout(() => {
                            document.getElementById("typingIndicator").innerText = "";
                        }, 1500);
                    }
                }
            };
        }

        async function loadContacts() {
            const res = await fetch(`/api/contacts?phone=${currentUser.phone}`);
            const contacts = await res.json();
            const container = document.getElementById("chatListContainer");
            container.innerHTML = "";
            contacts.forEach(c => {
                container.innerHTML += `
                    <div class="chat-item" onclick="selectChat('${c.contact_phone}', '${c.contact_name}')">
                        <div class="avatar">${c.contact_name.charAt(0).toUpperCase()}</div>
                        <div class="chat-info">
                            <div class="top-row">
                                <span class="name">${c.contact_name}</span>
                            </div>
                            <div class="preview">${c.contact_phone}</div>
                        </div>
                    </div>
                `;
            });
        }

        async function selectChat(phone, name) {
            activeRecipient = phone;
            document.getElementById("activeChatName").innerText = name;
            document.getElementById("activeChatAvatar").innerText = name.charAt(0).toUpperCase();
            document.getElementById("inputArea").style.display = "flex";

            const res = await fetch(`/api/messages?user1=${currentUser.phone}&user2=${phone}`);
            const messages = await res.json();
            const container = document.getElementById("messagesContainer");
            container.innerHTML = "";
            messages.forEach(m => appendMessage(m));
        }

        function appendMessage(msg) {
            const container = document.getElementById("messagesContainer");
            const isOutgoing = msg.sender === currentUser.phone;
            let contentHtml = msg.content;
            
            if (msg.msg_type === 'image') {
                contentHtml = `<img src="${msg.file_url}" style="max-width: 200px; border-radius: 6px;"/><br>${msg.content}`;
            } else if (msg.msg_type === 'file') {
                contentHtml = `<a href="${msg.file_url}" target="_blank" style="color: inherit;"><i class="fa-solid fa-file"></i> Download Document</a><br>${msg.content}`;
            }

            let reactions = JSON.parse(msg.reactions || "{}");
            let reactionsHtml = Object.entries(reactions).map(([emoji, count]) => `<span style="background:rgba(0,0,0,0.05); padding:2px 6px; border-radius:10px; font-size:11px; margin-right:2px;">${emoji} ${count}</span>`).join('');

            container.innerHTML += `
                <div class="message ${isOutgoing ? 'outgoing' : 'incoming'}" ondblclick="reactMessage(${msg.id})">
                    <div>${contentHtml}</div>
                    <div style="margin-top:4px;">${reactionsHtml}</div>
                    <div class="meta">
                        ${msg.timestamp} ${isOutgoing ? '<i class="fa-solid fa-check-double" style="color: #53bdeb;"></i>' : ''}
                    </div>
                </div>
            `;
            container.scrollTop = container.scrollHeight;
        }

        function sendMessage() {
            const input = document.getElementById("messageInput");
            const text = input.value.trim();
            if (!text || !activeRecipient) return;

            ws.send(JSON.stringify({
                type: "message",
                sender: currentUser.phone,
                recipient: activeRecipient,
                content: text,
                msg_type: "text"
            }));
            input.value = "";
        }

        function sendTypingSignal() {
            if (!activeRecipient) return;
            ws.send(JSON.stringify({
                type: "typing",
                sender: currentUser.phone,
                recipient: activeRecipient
            }));
        }

        function handleKeyPress(e) {
            if (e.key === 'Enter') sendMessage();
        }

        async function addContact() {
            const phone = document.getElementById("newContactPhone").value.trim();
            const name = document.getElementById("newContactName").value.trim();
            await fetch('/api/contacts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_phone: currentUser.phone, contact_phone: phone, contact_name: name })
            });
            closeModals();
            loadContacts();
        }

        function triggerFileUpload() { document.getElementById("fileInput").click(); }

        async function uploadFile(input) {
            if (!input.files[0] || !activeRecipient) return;
            const formData = new FormData();
            formData.append("file", input.files[0]);
            formData.append("sender", currentUser.phone);
            formData.append("recipient", activeRecipient);

            const res = await fetch('/api/upload', { method: 'POST', body: formData });
            const data = await res.json();
            ws.send(JSON.stringify({
                type: "message",
                sender: currentUser.phone,
                recipient: activeRecipient,
                content: input.files[0].name,
                msg_type: input.files[0].type.startsWith('image') ? 'image' : 'file',
                file_url: data.file_url
            }));
        }

        async function reactMessage(msgId) {
            const emoji = prompt("Choose reaction (❤️, 👍, 😂, 😮, 🙏):", "❤️");
            if (!emoji) return;
            await fetch('/api/react', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message_id: msgId, emoji: emoji })
            });
            // Refresh current chat messages
            selectChat(activeRecipient, document.getElementById("activeChatName").innerText);
        }

        async function postStatus() {
            const text = document.getElementById("statusTextInput").value.trim();
            if (!text) return;
            await fetch('/api/status', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ phone: currentUser.phone, content: text })
            });
            document.getElementById("statusTextInput").value = "";
            alert("Status posted!");
            closeModals();
        }

        async function openStatusModal() {
            document.getElementById("statusModal").classList.remove("hidden");
            const res = await fetch('/api/status');
            const statuses = await res.json();
            const feed = document.getElementById("statusFeed");
            feed.innerHTML = statuses.map(s => `<div style="padding: 8px; border-bottom: 1px solid var(--border-color);"><b>${s.phone}:</b> ${s.content} <span style="font-size:10px; color:var(--text-secondary);">${s.timestamp}</span></div>`).join('');
        }

        function openGroupModal() { document.getElementById("groupModal").classList.remove("hidden"); }
        function openNewContactModal() { document.getElementById("contactModal").classList.remove("hidden"); }
        function closeModals() { document.querySelectorAll('.modal').forEach(m => m.classList.add("hidden")); }

        function toggleTheme() {
            const body = document.body;
            body.dataset.theme = body.dataset.theme === "light" ? "dark" : "light";
        }
    </style>
</body>
</html>
    """

@app.post("/api/login")
async def login(data: dict):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (phone, username, about) VALUES (?, ?, ?)",
                   (data["phone"], data["username"], data["about"]))
    conn.commit()
    conn.close()
    return data

@app.get("/api/contacts")
async def get_contacts(phone: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT contact_phone, contact_name FROM contacts WHERE user_phone = ?", (phone,))
    rows = cursor.fetchall()
    conn.close()
    return [{"contact_phone": r[0], "contact_name": r[1]} for r in rows]

@app.post("/api/contacts")
async def add_contact(data: dict):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO contacts (user_phone, contact_phone, contact_name) VALUES (?, ?, ?)",
                   (data["user_phone"], data["contact_phone"], data["contact_name"]))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/messages")
async def get_messages(user1: str, user2: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, sender, recipient, content, msg_type, file_url, reactions, timestamp 
        FROM messages 
        WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
        ORDER BY id ASC
    """, (user1, user2, user2, user1))
    rows = cursor.fetchall()
    conn.close()
    return [{
        "id": r[0], "sender": r[1], "recipient": r[2], "content": r[3],
        "msg_type": r[4], "file_url": r[5], "reactions": r[6], "timestamp": r[7]
    } for r in rows]

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), sender: str = Form(...), recipient: str = Form(...)):
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"file_url": f"/static_uploads/{file.filename}"}

@app.post("/api/react")
async def react_message(data: dict):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT reactions FROM messages WHERE id = ?", (data["message_id"],))
    row = cursor.fetchone()
    if row:
        reactions = json.loads(row[0])
        emoji = data["emoji"]
        reactions[emoji] = reactions.get(emoji, 0) + 1
        cursor.execute("UPDATE messages SET reactions = ? WHERE id = ?", (json.dumps(reactions), data["message_id"]))
        conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/api/status")
async def post_status(data: dict):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO statuses (phone, content, timestamp) VALUES (?, ?, ?)",
                   (data["phone"], data["content"], datetime.now().strftime("%H:%M")))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/status")
async def get_statuses():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT phone, content, timestamp FROM statuses ORDER BY id DESC LIMIT 20")
    rows = cursor.fetchall()
    conn.close()
    return [{"phone": r[0], "content": r[1], "timestamp": r[2]} for r in rows]

@app.websocket("/ws/{phone}")
async def websocket_endpoint(websocket: WebSocket, phone: str):
    await manager.connect(phone, websocket)
    try:
        while True:
            data_str = await websocket.receive_text()
            data = json.loads(data_str)
            
            if data["type"] == "message":
                timestamp = datetime.now().strftime("%H:%M")
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO messages (sender, recipient, content, msg_type, file_url, timestamp) 
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (data["sender"], data["recipient"], data["content"], data.get("msg_type", "text"), data.get("file_url"), timestamp))
                msg_id = cursor.lastrowid
                conn.commit()
                conn.close()

                payload = {
                    "id": msg_id,
                    "type": "message",
                    "sender": data["sender"],
                    "recipient": data["recipient"],
                    "content": data["content"],
                    "msg_type": data.get("msg_type", "text"),
                    "file_url": data.get("file_url"),
                    "reactions": "{}",
                    "timestamp": timestamp
                }

                await manager.send_personal(payload, data["recipient"])
                await manager.send_personal(payload, data["sender"])
                
            elif data["type"] == "typing":
                await manager.send_personal({"type": "typing", "sender": data["sender"]}, data["recipient"])

    except WebSocketDisconnect:
        manager.disconnect(phone)

# Mount uploads directory for serving images/files
from fastapi.staticfiles import StaticFiles
app.mount("/static_uploads", StaticFiles(directory=UPLOAD_DIR), name="static_uploads")
