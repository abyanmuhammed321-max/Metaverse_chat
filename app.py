from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import json
import sqlite3
import uuid
from typing import Dict, List, Optional

app = FastAPI(title="Metaverse_WhatsApp - Clean Google Edition")

# ==================== DATABASE SETUP ====================
def init_db():
    conn = sqlite3.connect("metaverse_whatsapp_clean.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            recipient TEXT,
            type TEXT,
            content TEXT,
            view_once INTEGER DEFAULT 0,
            reactions TEXT DEFAULT '{}',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            display_name TEXT,
            status TEXT,
            profile_pic TEXT,
            is_google_user INTEGER DEFAULT 0
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
            view_once INTEGER DEFAULT 0,
            reactions TEXT DEFAULT '{}',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn

db_conn = init_db()
cursor = db_conn.cursor()

# ==================== WEBSOCKET MANAGER ====================
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
    display_name: Optional[str] = None
    status: Optional[str] = None
    profile_pic: Optional[str] = None
    is_google_user: Optional[int] = 0

@app.post("/user/update")
async def update_user(profile: UserProfile):
    cursor.execute("""
        INSERT INTO users (username, display_name, status, profile_pic, is_google_user) 
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET 
            display_name = COALESCE(?, display_name),
            status = COALESCE(?, status),
            profile_pic = COALESCE(?, profile_pic),
            is_google_user = COALESCE(?, is_google_user)
    """, (profile.username, profile.display_name, profile.status, profile.profile_pic, profile.is_google_user,
          profile.display_name, profile.status, profile.profile_pic, profile.is_google_user))
    db_conn.commit()
    return {"status": "success"}

@app.get("/user/{username}")
async def get_user(username: str):
    cursor.execute("SELECT display_name, status, profile_pic, is_google_user FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    if row:
        return {"display_name": row[0], "status": row[1], "profile_pic": row[2], "is_google_user": row[3]}
    return {"display_name": username, "status": "Using Metaverse WhatsApp", "profile_pic": None, "is_google_user": 0}

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

@app.get("/history/{user}/{contact}")
async def get_history(user: str, contact: str):
    cursor.execute("""
        SELECT id, sender, type, content, view_once, reactions FROM messages 
        WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
        ORDER BY id ASC
    """, (user, contact, contact, user))
    rows = cursor.fetchall()
    history = [{"id": r[0], "sender": r[1], "type": r[2], "content": r[3], "view_once": r[4], "reactions": json.loads(r[5] or '{}')} for r in rows]
    return {"history": history}

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
                        "sender": username, "message": content, "is_group": True,
                        "view_once": view_once, "reactions": {}
                    }
                    await manager.broadcast_to_group(recipient_id, payload, json.loads(row[0]))
                continue

            if msg_type in ["chat", "image"]:
                cursor.execute("INSERT INTO messages (sender, recipient, type, content, view_once) VALUES (?, ?, ?, ?, ?)", 
                               (username, recipient_id, msg_type, content, view_once))
                db_conn.commit()
                cursor.execute("SELECT last_insert_rowid()")
                msg_id = cursor.fetchone()[0]

                payload = {
                    "id": msg_id, "type": msg_type, "sender": username, "recipient_id": recipient_id,
                    "message": content, "view_once": view_once, "reactions": {}
                }
                # Send to recipient AND back to sender so it displays immediately
                await manager.send_personal_message(payload, recipient_id)
                if recipient_id != username:
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Metaverse WhatsApp - Google Clean Edition</title>
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <style>
        :root {
            --bg-primary: #0b141a;
            --bg-secondary: #111b21;
            --bg-panel: #202c33;
            --accent: #00a884;
            --text-main: #e9edef;
            --text-muted: #8696a0;
            --border: #222d34;
            --outgoing: #005c4b;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }
        body { background: var(--bg-primary); height: 100vh; display: flex; justify-content: center; align-items: center; color: var(--text-main); overflow: hidden; }
        .hidden { display: none !important; }
        
        #app-container { width: 95%; max-width: 1400px; height: 94vh; background: var(--bg-secondary); border: 1px solid var(--border); display: flex; box-shadow: 0 10px 30px rgba(0,0,0,0.5); border-radius: 12px; overflow: hidden; position: relative; }
        
        #login-screen { position: absolute; inset: 0; background: var(--bg-primary); display: flex; justify-content: center; align-items: center; z-index: 200; }
        #login-box { background: var(--bg-panel); border: 1px solid var(--border); padding: 40px; border-radius: 16px; text-align: center; width: 100%; max-width: 400px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
        #login-box h1 { color: var(--accent); margin-bottom: 8px; font-size: 24px; }
        #login-box p { color: var(--text-muted); font-size: 13px; margin-bottom: 24px; }

        .sidebar { width: 35%; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; height: 100%; }
        .sidebar-header { padding: 14px 16px; background: var(--bg-secondary); display: flex; align-items: center; justify-content: space-between; height: 70px; border-bottom: 1px solid var(--border); }
        .my-profile { font-weight: 600; color: var(--text-main); font-size: 14px; display: flex; align-items: center; gap: 10px; cursor: pointer; }
        .contact-avatar { width: 42px; height: 42px; border-radius: 50%; background: var(--accent); color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 16px; position: relative; flex-shrink: 0; overflow: hidden; }
        .contact-avatar img { width: 100%; height: 100%; object-fit: cover; }
        .online-dot { width: 10px; height: 10px; background: #00a884; border: 2px solid var(--bg-panel); border-radius: 50%; position: absolute; bottom: 0; right: 0; }
        
        .contacts-list { flex: 1; overflow-y: auto; }
        .contact-item { display: flex; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--border); cursor: pointer; transition: 0.2s; }
        .contact-item:hover, .contact-item.active { background: var(--bg-secondary); }
        .contact-details h4 { font-size: 14px; color: var(--text-main); font-weight: 500; }
        .contact-details p { font-size: 12px; color: var(--text-muted); margin-top: 2px; }

        .chat-panel { flex: 1; display: flex; flex-direction: column; background: var(--bg-primary); position: relative; height: 100%; }
        .chat-header { height: 70px; background: var(--bg-secondary); padding: 12px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); }
        .active-chat-info { display: flex; align-items: center; gap: 12px; cursor: pointer; }

        .chat-messages { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
        .message { max-width: 70%; padding: 10px 14px; border-radius: 8px; font-size: 14px; line-height: 20px; word-wrap: break-word; position: relative; }
        .message.incoming { background: var(--bg-panel); align-self: flex-start; color: var(--text-main); }
        .message.outgoing { background: var(--outgoing); align-self: flex-end; color: white; }

        .chat-input-area { height: 70px; background: var(--bg-secondary); padding: 12px 16px; display: flex; align-items: center; gap: 12px; border-top: 1px solid var(--border); }
        .chat-input-area input { flex: 1; padding: 10px 14px; border: none; border-radius: 8px; background: var(--bg-panel); color: var(--text-main); font-size: 14px; outline: none; }
        .action-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: var(--text-muted); }
        .action-btn:hover { color: var(--accent); }

        .modal-overlay { position: absolute; inset: 0; background: rgba(0,0,0,0.7); z-index: 300; display: flex; justify-content: center; align-items: center; }
        .modal-content { background: var(--bg-panel); padding: 24px; border-radius: 12px; width: 100%; max-width: 380px; text-align: center; border: 1px solid var(--border); }
        .sel-btn { background: var(--accent); border: none; color: white; padding: 10px 16px; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 13px; width: 100%; margin-top: 12px; }
    </style>
</head>
<body>

    <div id="app-container">
        <!-- Login Screen with Google Sign In -->
        <div id="login-screen">
            <div id="login-box">
                <h1>💬 Metaverse</h1>
                <p>Sign in with your Google account to chat securely and share your profile.</p>
                <div id="g_id_onload"
                     data-client_id="358332042325-3s7o118sjfv1qug4r6qlmf534083ti10.apps.googleusercontent.com"
                     data-callback="handleGoogleLogin"
                     data-auto_prompt="false">
                </div>
                <div class="g_id_signin"
                     data-type="standard"
                     data-shape="rectangular"
                     data-theme="outline"
                     data-text="sign_in_with"
                     data-size="large"
                     data-logo_alignment="left">
                </div>
                <div style="margin-top: 20px;">
                    <input type="text" id="manualUsernameInput" placeholder="Or enter username..." style="width: 100%; padding: 10px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 6px; color: white; font-size: 13px; margin-bottom: 10px; outline: none;">
                    <button class="sel-btn" onclick="manualLogin()">Guest Login</button>
                </div>
            </div>
        </div>

        <!-- Sidebar -->
        <div class="sidebar">
            <div class="sidebar-header">
                <div class="my-profile">
                    <div class="contact-avatar" id="myAvatarDisplay">?</div>
                    <span id="myProfileNameDisplay">User</span>
                </div>
                <button class="action-btn" onclick="logout()" title="Logout" style="font-size: 13px; color: var(--text-muted);">Logout</button>
            </div>
            <div class="contacts-list" id="contactsListContainer"></div>
        </div>

        <!-- Chat Panel -->
        <div class="chat-panel">
            <div class="chat-header">
                <div class="active-chat-info" onclick="openContactProfile()">
                    <div class="contact-avatar" id="activeChatAvatar">?</div>
                    <div>
                        <h4 id="activeChatTitle">Select Contact</h4>
                        <p id="activeChatStatus" style="font-size: 11px; color: var(--text-muted);">Offline</p>
                    </div>
                </div>
            </div>

            <div class="chat-messages" id="chatMessagesContainer">
                <div style="text-align: center; margin: auto; color: var(--text-muted); font-size: 13px;">
                    <p>🔒 Select a contact from the sidebar to start chatting.</p>
                </div>
            </div>

            <div class="chat-input-area">
                <label class="action-btn" title="Send Image">📎<input type="file" id="imageInput" accept="image/*" style="display:none;" onchange="sendImage(event)"></label>
                <input type="text" id="messageInput" placeholder="Type a message..." onkeypress="handleKey(event)" disabled>
                <button class="action-btn" onclick="sendMessage()" style="color: var(--accent);" title="Send">➤</button>
            </div>
        </div>

        <!-- Contact Profile Modal -->
        <div id="contact-profile-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <div class="contact-avatar" id="modalProfileAvatar" style="width: 70px; height: 70px; font-size: 28px; margin: 0 auto 12px auto;">?</div>
                <h3 id="modalProfileName" style="margin-bottom: 4px; color: var(--text-main);">Name</h3>
                <p id="modalProfileEmail" style="color: var(--text-muted); font-size: 12px; margin-bottom: 4px;"></p>
                <p id="modalProfileStatus" style="color: var(--accent); font-size: 12px; margin-bottom: 16px;">Google Verified Profile</p>
                <div id="addToContactsBtnWrapper"></div>
                <button class="sel-btn" style="background: var(--bg-secondary); border: 1px solid var(--border); margin-top: 8px;" onclick="closeContactProfile()">Close</button>
            </div>
        </div>
    </div>

    <script>
        let ws;
        let currentUser = localStorage.getItem("metaverse_user") || null;
        let userDisplayName = localStorage.getItem("metaverse_name") || "";
        let userProfilePic = localStorage.getItem("metaverse_pic") || "";
        let isGoogleUser = parseInt(localStorage.getItem("metaverse_is_google") || "0");
        
        let onlineUsers = [];
        let savedContacts = [];
        let activeContact = null;
        let chatHistories = {};
        let contactDetailsMap = {};

        window.onload = async function() {
            if (currentUser) {
                await fetchUserData(currentUser);
                initializeSession(currentUser);
            }
        };

        function decodeJwtResponse(token) {
            let base64Url = token.split('.')[1];
            let base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
            let jsonPayload = decodeURIComponent(atob(base64).split('').map(c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)).join(''));
            return JSON.parse(jsonPayload);
        }

        async function handleGoogleLogin(response) {
            const responsePayload = decodeJwtResponse(response.credential);
            currentUser = responsePayload.email;
            userDisplayName = responsePayload.name;
            userProfilePic = responsePayload.picture;
            isGoogleUser = 1;

            localStorage.setItem("metaverse_user", currentUser);
            localStorage.setItem("metaverse_name", userDisplayName);
            localStorage.setItem("metaverse_pic", userProfilePic);
            localStorage.setItem("metaverse_is_google", "1");

            await saveProfileToBackend();
            initializeSession(currentUser);
        }

        async function manualLogin() {
            const val = document.getElementById("manualUsernameInput").value.trim();
            if (!val) return alert("Enter a username.");
            currentUser = val;
            userDisplayName = val;
            userProfilePic = "";
            isGoogleUser = 0;

            localStorage.setItem("metaverse_user", currentUser);
            localStorage.setItem("metaverse_name", userDisplayName);
            localStorage.removeItem("metaverse_pic");
            localStorage.setItem("metaverse_is_google", "0");

            await saveProfileToBackend();
            initializeSession(currentUser);
        }

        async function saveProfileToBackend() {
            await fetch("/user/update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    username: currentUser,
                    display_name: userDisplayName,
                    status: "Using Metaverse WhatsApp",
                    profile_pic: userProfilePic,
                    is_google_user: isGoogleUser
                })
            });
        }

        async function fetchUserData(username) {
            const res = await fetch(`/user/${encodeURIComponent(username)}`);
            const data = await res.json();
            userDisplayName = data.display_name || username;
            userProfilePic = data.profile_pic || "";
            isGoogleUser = data.is_google_user || 0;
        }

        async function initializeSession(username) {
            document.getElementById("login-screen").classList.add("hidden");
            document.getElementById("myProfileNameDisplay").innerText = userDisplayName;
            if (userProfilePic) {
                document.getElementById("myAvatarDisplay").innerHTML = `<img src="${userProfilePic}">`;
            } else {
                document.getElementById("myAvatarDisplay").innerText = userDisplayName.charAt(0).toUpperCase();
            }
            connectWebSocket();
            await fetchSavedContacts();
        }

        function logout() {
            localStorage.clear();
            location.reload();
        }

        async function fetchSavedContacts() {
            const res = await fetch(`/contacts/${encodeURIComponent(currentUser)}`);
            const data = await res.json();
            savedContacts = data.contacts;
            renderContacts();
        }

        function connectWebSocket() {
            const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
            ws = new WebSocket(`${wsProtocol}${window.location.host}/ws/${encodeURIComponent(currentUser)}`);
            
            ws.onmessage = async function(event) {
                const data = JSON.parse(event.data);
                
                if (data.type === "user_list") {
                    onlineUsers = data.users.filter(u => u !== currentUser);
                    renderContacts();
                } else {
                    const sender = data.sender === currentUser ? data.recipient_id : data.sender;
                    const targetChat = data.recipient_id === currentUser ? data.sender : data.recipient_id;
                    const key = activeContact === data.sender ? data.sender : targetChat;

                    if (!chatHistories[key]) chatHistories[key] = [];
                    chatHistories[key].push(data);
                    if (activeContact === key) renderMessages();
                }
            };

            ws.onclose = function() {
                setTimeout(connectWebSocket, 3000);
            };
        }

        async function renderContacts() {
            const container = document.getElementById("contactsListContainer");
            container.innerHTML = "";
            const allSet = new Set([...onlineUsers, ...savedContacts]);

            for (let email of allSet) {
                if (email === currentUser) continue;
                
                // Fetch profile details for Google profile display
                let res = await fetch(`/user/${encodeURIComponent(email)}`);
                let details = await res.json();
                contactDetailsMap[email] = details;

                const isOnline = onlineUsers.includes(email);
                const isActive = activeContact === email ? "active" : "";
                let avatarHtml = details.profile_pic ? `<img src="${details.profile_pic}">` : email.charAt(0).toUpperCase();

                container.innerHTML += `
                    <div class="contact-item ${isActive}" onclick="selectContact('${email}')">
                        <div class="contact-avatar">${avatarHtml}<div class="${isOnline ? 'online-dot' : ''}"></div></div>
                        <div class="contact-details" style="margin-left: 12px;">
                            <h4>${details.display_name || email}</h4>
                            <p>${isOnline ? 'Online' : 'Offline'}</p>
                        </div>
                    </div>
                `;
            }
        }

        async function selectContact(email) {
            activeContact = email;
            const details = contactDetailsMap[email] || { display_name: email };
            document.getElementById("activeChatTitle").innerText = details.display_name || email;
            document.getElementById("activeChatStatus").innerText = onlineUsers.includes(email) ? "Online" : "Offline";
            
            const avatarContainer = document.getElementById("activeChatAvatar");
            if (details.profile_pic) {
                avatarContainer.innerHTML = `<img src="${details.profile_pic}">`;
            } else {
                avatarContainer.innerText = email.charAt(0).toUpperCase();
            }

            document.getElementById("messageInput").disabled = false;
            
            const res = await fetch(`/history/${encodeURIComponent(currentUser)}/${encodeURIComponent(email)}`);
            const data = await res.json();
            chatHistories[email] = data.history;
            renderMessages();
        }

        function openContactProfile() {
            if (!activeContact) return;
            const details = contactDetailsMap[activeContact] || {};
            document.getElementById("modalProfileName").innerText = details.display_name || activeContact;
            document.getElementById("modalProfileEmail").innerText = details.is_google_user ? activeContact : "";
            document.getElementById("modalProfileStatus").innerText = details.is_google_user ? "✓ Google Verified Account" : "Guest Account";
            
            const avatarModal = document.getElementById("modalProfileAvatar");
            if (details.profile_pic) {
                avatarModal.innerHTML = `<img src="${details.profile_pic}">`;
            } else {
                avatarModal.innerText = activeContact.charAt(0).toUpperCase();
            }

            const btnWrapper = document.getElementById("addToContactsBtnWrapper");
            if (savedContacts.includes(activeContact)) {
                btnWrapper.innerHTML = `<p style="color: var(--accent); font-weight: 600; font-size: 13px; margin-top: 10px;">✓ Saved in Contacts</p>`;
            } else {
                btnWrapper.innerHTML = `<button class="sel-btn" onclick="addCurrentContactPermanent()">Add to Contacts</button>`;
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

        function renderMessages() {
            const container = document.getElementById("chatMessagesContainer");
            container.innerHTML = "";
            const messages = chatHistories[activeContact] || [];

            messages.forEach(msg => {
                const isOutgoing = msg.sender === currentUser;
                let contentHTML = "";

                if (msg.type === "image") {
                    contentHTML = `<img src="${msg.content}" style="max-width: 200px; border-radius: 6px;">`;
                } else {
                    contentHTML = `<span>${msg.content}</span>`;
                }

                container.innerHTML += `
                    <div class="message ${isOutgoing ? "outgoing" : "incoming"}">
                        <div>${contentHTML}</div>
                    </div>
                `;
            });
            container.scrollTop = container.scrollHeight;
        }

        function sendMessage() {
            const input = document.getElementById("messageInput");
            const text = input.value.trim();
            if (!text || !activeContact) return;

            ws.send(JSON.stringify({ type: "chat", recipient_id: activeContact, message: text }));
            input.value = "";
        }

        function handleKey(e) { if (e.key === "Enter") sendMessage(); }

        function sendImage(e) {
            const file = e.target.files[0];
            if (!file || !activeContact) return;
            const reader = new FileReader();
            reader.onload = function() {
                ws.send(JSON.stringify({ type: "image", recipient_id: activeContact, message: reader.result }));
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
