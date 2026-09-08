import sqlite3
import json
from datetime import datetime
from typing import Dict, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="Metaverse_Chat", version="3.0.0")

# --- DATABASE SETUP ---
DB_FILE = "metaverse_chat.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT,
            email TEXT,
            status TEXT DEFAULT 'Hey there! I am using Metaverse_Chat',
            avatar TEXT DEFAULT 'https://api.dicebear.com/7.x/bottts/svg?seed=default'
        )
    ''')
    # Messages table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            recipient TEXT,
            content TEXT,
            msg_type TEXT DEFAULT 'text',
            timestamp TEXT,
            reactions TEXT DEFAULT '{}'
        )
    ''')
    # Groups table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY,
            name TEXT,
            members TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- WEBSOCKET CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        self.active_connections[username] = websocket
        await self.broadcast_user_status()

    def disconnect(self, username: str):
        if username in self.active_connections:
            del self.active_connections[username]

    async def broadcast_user_status(self):
        users = list(self.active_connections.keys())
        data = json.dumps({"type": "online_users", "users": users})
        for connection in self.active_connections.values():
            await connection.send_text(data)

    async def send_personal(self, message: dict, username: str):
        if username in self.active_connections:
            await self.active_connections[username].send_text(json.dumps(message))

    async def broadcast_group(self, message: dict, members: List[str]):
        for member in members:
            if member in self.active_connections:
                await self.active_connections[member].send_text(json.dumps(message))

manager = ConnectionManager()

# --- PYDANTIC MODELS ---
class UserAuth(BaseModel):
    username: str
    password: str = ""
    email: str = ""
    avatar: str = ""

class GroupCreate(BaseModel):
    group_id: str
    name: str
    members: List[str]

# --- REST API ENDPOINTS ---
@app.post("/api/login")
def login(user: UserAuth):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT username, status, avatar, email FROM users WHERE username = ?", (user.username,))
    row = cursor.fetchone()
    
    if not row:
        avatar = user.avatar or f"https://api.dicebear.com/7.x/bottts/svg?seed={user.username}"
        cursor.execute("INSERT INTO users (username, password, email, avatar) VALUES (?, ?, ?, ?)", 
                       (user.username, user.password, user.email, avatar))
        conn.commit()
        user_data = {"username": user.username, "status": "Hey there! I am using Metaverse_Chat", "avatar": avatar, "email": user.email}
    else:
        user_data = {"username": row[0], "status": row[1], "avatar": row[2], "email": row[3]}
    
    conn.close()
    return {"success": True, "user": user_data}

@app.post("/api/google-login")
def google_login(user: UserAuth):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT username, status, avatar, email FROM users WHERE email = ? OR username = ?", (user.email, user.username))
    row = cursor.fetchone()
    
    if not row:
        avatar = user.avatar or f"https://api.dicebear.com/7.x/bottts/svg?seed={user.username}"
        cursor.execute("INSERT INTO users (username, password, email, avatar) VALUES (?, ?, ?, ?)", 
                       (user.username, "", user.email, avatar))
        conn.commit()
        user_data = {"username": user.username, "status": "Hey there! I am using Metaverse_Chat", "avatar": avatar, "email": user.email}
    else:
        user_data = {"username": row[0], "status": row[1], "avatar": row[2], "email": row[3]}
    
    conn.close()
    return {"success": True, "user": user_data}

@app.get("/api/messages/{user1}/{user2}")
def get_messages(user1: str, user2: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if user2.startswith("group_"):
        cursor.execute("SELECT id, sender, recipient, content, msg_type, timestamp, reactions FROM messages WHERE recipient = ? ORDER BY id ASC", (user2,))
    else:
        cursor.execute('''
            SELECT id, sender, recipient, content, msg_type, timestamp, reactions FROM messages 
            WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
            ORDER BY id ASC
        ''', (user1, user2, user2, user1))
    rows = cursor.fetchall()
    conn.close()
    
    messages = []
    for r in rows:
        messages.append({
            "id": r[0],
            "sender": r[1],
            "recipient": r[2],
            "content": r[3],
            "msg_type": r[4],
            "timestamp": r[5],
            "reactions": json.loads(r[6] or "{}")
        })
    return messages

@app.post("/api/groups")
def create_group(group: GroupCreate):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO groups (group_id, name, members) VALUES (?, ?, ?)",
                   (group.group_id, group.name, json.dumps(group.members)))
    conn.commit()
    conn.close()
    return {"success": True}

@app.get("/api/groups")
def get_groups():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT group_id, name, members FROM groups")
    rows = cursor.fetchall()
    conn.close()
    groups = [{"group_id": r[0], "name": r[1], "members": json.loads(r[2])} for r in rows]
    return groups

# --- WEBSOCKET ENDPOINT ---
@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(websocket, username)
    try:
        while True:
            data_text = await websocket.receive_text()
            data = json.loads(data_text)
            msg_type = data.get("type", "chat")
            
            if msg_type == "chat":
                recipient = data.get("recipient")
                content = data.get("content")
                m_type = data.get("msg_type", "text")
                timestamp = datetime.now().strftime("%H:%M")
                
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute("INSERT INTO messages (sender, recipient, content, msg_type, timestamp, reactions) VALUES (?, ?, ?, ?, ?, ?)",
                               (username, recipient, content, m_type, timestamp, "{}"))
                msg_id = cursor.lastrowid
                conn.commit()
                conn.close()
                
                msg_payload = {
                    "type": "chat",
                    "id": msg_id,
                    "sender": username,
                    "recipient": recipient,
                    "content": content,
                    "msg_type": m_type,
                    "timestamp": timestamp,
                    "reactions": {}
                }
                
                if recipient.startswith("group_"):
                    # Fetch group members and broadcast
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient,))
                    row = cursor.fetchone()
                    conn.close()
                    if row:
                        members = json.loads(row[0])
                        await manager.broadcast_group(msg_payload, members)
                else:
                    if recipient in manager.active_connections:
                        await manager.send_personal(msg_payload, recipient)
                    await manager.send_personal(msg_payload, username)

            elif msg_type == "reaction":
                msg_id = data.get("msg_id")
                emoji = data.get("emoji")
                recipient = data.get("recipient")
                
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute("SELECT reactions FROM messages WHERE id = ?", (msg_id,))
                row = cursor.fetchone()
                if row:
                    reactions = json.loads(row[0] or "{}")
                    reactions[username] = emoji
                    cursor.execute("UPDATE messages SET reactions = ? WHERE id = ?", (json.dumps(reactions), msg_id))
                    conn.commit()
                conn.close()
                
                reaction_payload = {
                    "type": "reaction",
                    "msg_id": msg_id,
                    "reactions": reactions,
                    "recipient": recipient
                }
                if recipient.startswith("group_"):
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    cursor.execute("SELECT members FROM groups WHERE group_id = ?", (recipient,))
                    row = cursor.fetchone()
                    conn.close()
                    if row:
                        await manager.broadcast_group(reaction_payload, json.loads(row[0]))
                else:
                    if recipient in manager.active_connections:
                        await manager.send_personal(reaction_payload, recipient)
                    await manager.send_personal(reaction_payload, username)

    except WebSocketDisconnect:
        manager.disconnect(username)
        await manager.broadcast_user_status()

# --- FRONTEND UI ---
@app.get("/", response_class=HTMLResponse)
def get_chat_ui():
    return """
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Metaverse_Chat</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        wa: {
                            dark: '#111b21',
                            panel: '#202c33',
                            hover: '#2a3942',
                            green: '#00a884',
                            lightgreen: '#005c4b',
                            bubble: '#005c4b',
                            incoming: '#202c33',
                            bg: '#0b141a',
                            border: '#374151'
                        }
                    }
                }
            }
        }
    </script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://accounts.google.com/gsi/client" async defer></script>
</head>
<body class="bg-wa-bg text-gray-100 h-screen flex flex-col overflow-hidden select-none">

    <!-- LOGIN SCREEN -->
    <div id="login-screen" class="fixed inset-0 bg-gray-950 z-50 flex items-center justify-center">
        <div class="bg-wa-panel p-8 rounded-2xl shadow-2xl border border-gray-800 w-96 text-center">
            <div class="text-wa-green text-6xl mb-4"><i class="fa-solid fa-earth-americas"></i></div>
            <h2 class="text-2xl font-bold mb-1">Metaverse_Chat</h2>
            <p class="text-gray-400 text-sm mb-6">WhatsApp Redefined in the Metaverse</p>
            
            <input type="text" id="username-input" placeholder="Choose Username" class="w-full bg-wa-dark border border-gray-700 rounded-lg px-4 py-3 mb-4 focus:outline-none focus:border-wa-green text-white text-sm">
            <button onclick="manualLogin()" class="w-full bg-wa-green hover:bg-emerald-600 text-white font-semibold py-3 rounded-lg transition duration-200 mb-4 shadow-lg">Start Chatting Manually</button>
            
            <div class="relative flex py-2 items-center">
                <div class="flex-grow border-t border-gray-700"></div>
                <span class="flex-shrink mx-4 text-gray-400 text-xs">OR</span>
                <div class="flex-grow border-t border-gray-700"></div>
            </div>

            <!-- Google Sign-In Button Container -->
            <div id="g_id_onload"
                 data-client_id="358332042325-3s7o118sjfv1qug4r6qlmf534083ti10.apps.googleusercontent.com"
                 data-callback="handleGoogleResponse"
                 data-auto_prompt="false">
            </div>
            <div class="g_id_signin flex justify-center mt-4" data-type="standard" data-shape="rectangular" data-theme="filled_black" data-text="sign_in_with" data-size="large"></div>
        </div>
    </div>

    <!-- MAIN CHAT APP -->
    <div id="app-screen" class="flex h-full w-full hidden overflow-hidden shadow-2xl">
        <!-- Sidebar -->
        <div class="w-1/3 min-w-[320px] bg-wa-panel border-r border-gray-800 flex flex-col">
            <!-- User Header -->
            <div class="p-4 bg-wa-panel border-b border-gray-800 flex items-center justify-between">
                <div class="flex items-center space-x-3 cursor-pointer" onclick="openProfileModal()">
                    <img id="my-avatar" src="" class="w-10 h-10 rounded-full object-cover">
                    <div>
                        <span id="my-username" class="font-bold text-sm block"></span>
                        <span id="my-status-text" class="text-xs text-gray-400 truncate max-w-[120px] block">Online</span>
                    </div>
                </div>
                <div class="text-gray-400 space-x-4 text-lg">
                    <i class="fa-solid fa-users cursor-pointer hover:text-white" title="Create Group" onclick="openGroupModal()"></i>
                    <i class="fa-solid fa-moon cursor-pointer hover:text-white" title="Toggle Theme" onclick="toggleTheme()"></i>
                    <i class="fa-solid fa-right-from-bracket cursor-pointer hover:text-white" title="Logout" onclick="logout()"></i>
                </div>
            </div>

            <!-- Search Bar -->
            <div class="p-3 bg-wa-dark/40 flex items-center space-x-2">
                <div class="flex-1 bg-wa-panel border border-gray-700 rounded-lg px-3 py-1.5 flex items-center space-x-2">
                    <i class="fa-solid fa-magnifying-glass text-gray-400 text-sm"></i>
                    <input type="text" id="search-chat" placeholder="Search or start new chat" oninput="filterChats()" class="w-full bg-transparent text-sm focus:outline-none text-white">
                </div>
            </div>

            <!-- Chat & Contact List -->
            <div id="users-list" class="flex-1 overflow-y-auto divide-y divide-gray-800/50">
                <!-- Populated Dynamically -->
            </div>
        </div>

        <!-- Chat Window -->
        <div class="flex-2 flex flex-col bg-wa-bg w-full relative">
            <!-- Chat Header -->
            <div id="chat-header" class="p-3.5 bg-wa-panel border-b border-gray-800 flex items-center justify-between">
                <div class="flex items-center space-x-3">
                    <img id="header-avatar" src="https://api.dicebear.com/7.x/bottts/svg?seed=Global" class="w-10 h-10 rounded-full object-cover bg-gray-700">
                    <div>
                        <h3 id="chat-title" class="font-bold text-sm">Global Chat</h3>
                        <span id="chat-status" class="text-xs text-wa-green">Public Metaverse Room</span>
                    </div>
                </div>
                <div class="flex items-center space-x-5 text-gray-400 text-lg">
                    <i class="fa-solid fa-video cursor-pointer hover:text-white" title="Video Call" onclick="startCall('video')"></i>
                    <i class="fa-solid fa-phone cursor-pointer hover:text-white" title="Voice Call" onclick="startCall('audio')"></i>
                    <i class="fa-solid fa-ellipsis-vertical cursor-pointer hover:text-white"></i>
                </div>
            </div>

            <!-- Messages Area -->
            <div id="messages-container" class="flex-1 p-6 overflow-y-auto space-y-3 flex flex-col bg-[radial-gradient(#1f2c34_1px,transparent_1px)] [background-size:16px_16px]">
                <!-- Populated Dynamically -->
            </div>

            <!-- Input Area -->
            <div class="p-3 bg-wa-panel border-t border-gray-800 flex items-center space-x-3">
                <label class="text-gray-400 cursor-pointer hover:text-white text-lg px-1">
                    <i class="fa-solid fa-paperclip"></i>
                    <input type="file" id="image-input" accept="image/*" class="hidden" onchange="sendImage(event)">
                </label>
                
                <input type="text" id="message-input" placeholder="Type a message" class="flex-1 bg-wa-dark border border-gray-700 rounded-lg px-4 py-2.5 focus:outline-none focus:border-wa-green text-sm text-white" onkeypress="handleKeyPress(event)">
                
                <button id="mic-btn" onclick="toggleVoiceRecord()" class="text-gray-400 hover:text-wa-green text-lg px-1" title="Record Voice Note"><i class="fa-solid fa-microphone"></i></button>
                <button onclick="sendMessage()" class="bg-wa-green hover:bg-emerald-600 text-white px-4 py-2.5 rounded-lg transition duration-200"><i class="fa-solid fa-paper-plane text-sm"></i></button>
            </div>
        </div>
    </div>

    <!-- MODAL: CREATE GROUP -->
    <div id="group-modal" class="fixed inset-0 bg-black/70 z-50 hidden flex items-center justify-center">
        <div class="bg-wa-panel p-6 rounded-xl border border-gray-800 w-96">
            <h3 class="text-lg font-bold mb-4">Create New Group</h3>
            <input type="text" id="group-name-input" placeholder="Group Name" class="w-full bg-wa-dark border border-gray-700 rounded-lg px-3 py-2 mb-4 text-sm focus:outline-none focus:border-wa-green text-white">
            <p class="text-xs text-gray-400 mb-2">Select Members:</p>
            <div id="group-members-list" class="max-h-40 overflow-y-auto space-y-2 mb-4 bg-wa-dark p-2 rounded-lg"></div>
            <div class="flex justify-end space-x-2">
                <button onclick="closeGroupModal()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm">Cancel</button>
                <button onclick="createGroup()" class="px-4 py-2 bg-wa-green hover:bg-emerald-600 rounded-lg text-sm font-semibold">Create</button>
            </div>
        </div>
    </div>

    <!-- SCRIPT LOGIC -->
    <script>
        let currentUser = "";
        let currentUserAvatar = "";
        let currentRecipient = "Global";
        let ws = null;
        let onlineUsers = [];
        let groups = [];
        let mediaRecorder = null;
        let audioChunks = [];
        let isRecording = false;

        // Auto-login check on page load
        window.onload = function() {
            const savedUser = localStorage.getItem("metaverse_chat_user");
            if (savedUser) {
                const userObj = JSON.parse(savedUser);
                currentUser = userObj.username;
                currentUserAvatar = userObj.avatar;
                document.getElementById("my-username").innerText = currentUser;
                document.getElementById("my-avatar").src = currentUserAvatar;
                document.getElementById("login-screen").classList.add("hidden");
                document.getElementById("app-screen").classList.remove("hidden");
                connectWebSocket();
                fetchGroups();
            }
        };

        async function manualLogin() {
            const username = document.getElementById("username-input").value.trim();
            if (!username) return alert("Please enter a valid username");

            const res = await fetch('/api/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: username, password: "", email: username + "@metaverse.chat"})
            });
            const data = await res.json();
            if (data.success) {
                completeLogin(data.user);
            }
        }

        function handleGoogleResponse(response) {
            // Decode Google JWT credential token payload
            const base64Url = response.credential.split('.')[1];
            const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
            const jsonPayload = decodeURIComponent(atob(base64).split('').map(function(c) {
                return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
            }).join(''));
            const googleUser = JSON.parse(jsonPayload);

            fetch('/api/google-login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    username: googleUser.name.replace(/\\s+/g, '_'),
                    email: googleUser.email,
                    avatar: googleUser.picture
                })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    completeLogin(data.user);
                }
            });
        }

        function completeLogin(user) {
            currentUser = user.username;
            currentUserAvatar = user.avatar;
            localStorage.setItem("metaverse_chat_user", JSON.stringify(user));
            
            document.getElementById("my-username").innerText = currentUser;
            document.getElementById("my-avatar").src = currentUserAvatar;
            document.getElementById("login-screen").classList.add("hidden");
            document.getElementById("app-screen").classList.remove("hidden");

            connectWebSocket();
            fetchGroups();
        }

        function logout() {
            localStorage.removeItem("metaverse_chat_user");
            if (ws) ws.close();
            location.reload();
        }

        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/ws/${currentUser}`);

            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                if (data.type === "online_users") {
                    onlineUsers = data.users.filter(u => u !== currentUser);
                    renderUsersAndGroupsList();
                } else if (data.type === "chat") {
                    if (data.recipient === currentRecipient || data.sender === currentRecipient || (data.recipient === "Global" && currentRecipient === "Global")) {
                        appendMessageToUI(data);
                    }
                } else if (data.type === "reaction") {
                    if (data.recipient === currentRecipient || currentRecipient === "Global") {
                        updateMessageReactions(data.msg_id, data.reactions);
                    }
                }
            };
        }

        async function fetchGroups() {
            const res = await fetch('/api/groups');
            groups = await res.json();
            renderUsersAndGroupsList();
        }

        function renderUsersAndGroupsList() {
            const listContainer = document.getElementById("users-list");
            let html = `
                <div onclick="selectRecipient('Global')" class="p-3 hover:bg-wa-hover cursor-pointer flex items-center space-x-3 transition ${currentRecipient === 'Global' ? 'bg-wa-hover' : ''}">
                    <div class="w-11 h-11 rounded-full bg-wa-green flex items-center justify-center text-white text-lg"><i class="fa-solid fa-earth-americas"></i></div>
                    <div class="flex-1">
                        <div class="font-semibold text-sm">Global Chat</div>
                        <div class="text-xs text-gray-400">Public Room</div>
                    </div>
                </div>
            `;

            // Render Groups
            groups.forEach(g => {
                html += `
                    <div onclick="selectRecipient('${g.group_id}')" class="p-3 hover:bg-wa-hover cursor-pointer flex items-center space-x-3 transition ${currentRecipient === g.group_id ? 'bg-wa-hover' : ''}">
                        <div class="w-11 h-11 rounded-full bg-purple-600 flex items-center justify-center text-white text-lg"><i class="fa-solid fa-users-rectangle"></i></div>
                        <div class="flex-1">
                            <div class="font-semibold text-sm">${g.name}</div>
                            <div class="text-xs text-gray-400">Group Room</div>
                        </div>
                    </div>
                `;
            });

            // Render Online Users
            onlineUsers.forEach(user => {
                html += `
                    <div onclick="selectRecipient('${user}')" class="p-3 hover:bg-wa-hover cursor-pointer flex items-center space-x-3 transition ${currentRecipient === user ? 'bg-wa-hover' : ''}">
                        <img src="https://api.dicebear.com/7.x/bottts/svg?seed=${user}" class="w-11 h-11 rounded-full object-cover bg-gray-700">
                        <div class="flex-1">
                            <div class="font-semibold text-sm">${user}</div>
                            <div class="text-xs text-wa-green flex items-center space-x-1"><span class="w-2 h-2 rounded-full bg-wa-green inline-block"></span><span>Online</span></div>
                        </div>
                    </div>
                `;
            });

            listContainer.innerHTML = html;
        }

        async function selectRecipient(recipient) {
            currentRecipient = recipient;
            let title = recipient;
            let avatar = `https://api.dicebear.com/7.x/bottts/svg?seed=${recipient}`;
            
            if (recipient === "Global") {
                title = "Global Chat";
                avatar = "https://api.dicebear.com/7.x/bottts/svg?seed=Global";
            } else if (recipient.startsWith("group_")) {
                const g = groups.find(x => x.group_id === recipient);
                if (g) title = g.name;
                avatar = "https://api.dicebear.com/7.x/bottts/svg?seed=" + recipient;
            }

            document.getElementById("chat-title").innerText = title;
            document.getElementById("header-avatar").src = avatar;
            document.getElementById("chat-status").innerText = recipient.startsWith("group_") ? "Group Chat" : (onlineUsers.includes(recipient) ? "Online" : "Active in Metaverse");
            
            renderUsersAndGroupsList();

            const container = document.getElementById("messages-container");
            container.innerHTML = "";
            
            const res = await fetch(`/api/messages/${currentUser}/${recipient}`);
            const messages = await res.json();
            messages.forEach(msg => appendMessageToUI(msg));
        }

        function sendMessage() {
            const input = document.getElementById("message-input");
            const content = input.value.trim();
            if (!content) return;

            const messageData = {
                type: "chat",
                recipient: currentRecipient,
                content: content,
                msg_type: "text"
            };

            ws.send(JSON.stringify(messageData));
            input.value = "";
        }

        function sendImage(e) {
            const file = e.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = function(uploadEvent) {
                const base64Image = uploadEvent.target.result;
                const messageData = {
                    type: "chat",
                    recipient: currentRecipient,
                    content: base64Image,
                    msg_type: "image"
                };
                ws.send(JSON.stringify(messageData));
            };
            reader.readAsDataURL(file);
        }

        async function toggleVoiceRecord() {
            const micBtn = document.getElementById("mic-btn");
            if (!isRecording) {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    mediaRecorder = new MediaRecorder(stream);
                    audioChunks = [];
                    
                    mediaRecorder.ondataavailable = event => {
                        audioChunks.push(event.data);
                    };

                    mediaRecorder.onstop = () => {
                        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                        const reader = new FileReader();
                        reader.onload = function() {
                            const base64Audio = reader.result;
                            const messageData = {
                                type: "chat",
                                recipient: currentRecipient,
                                content: base64Audio,
                                msg_type: "audio"
                            };
                            ws.send(JSON.stringify(messageData));
                        };
                        reader.readAsDataURL(audioBlob);
                    };

                    mediaRecorder.start();
                    isRecording = true;
                    micBtn.classList.add("text-red-500", "animate-pulse");
                } catch (err) {
                    alert("Microphone access denied or unavailable.");
                }
            } else {
                mediaRecorder.stop();
                isRecording = false;
                micBtn.classList.remove("text-red-500", "animate-pulse");
            }
        }

        function appendMessageToUI(msg) {
            const container = document.getElementById("messages-container");
            const isMe = msg.sender === currentUser;
            
            const div = document.createElement("div");
            div.className = `flex flex-col max-w-[65%] mb-2 ${isMe ? 'ml-auto items-end' : 'mr-auto items-start'}`;
            div.id = `msg-${msg.id}`;
            
            let contentHtml = '';
            if (msg.msg_type === 'image') {
                contentHtml = `<img src="${msg.content}" class="rounded-lg max-h-60 object-cover cursor-pointer mb-1" onclick="window.open(this.src)">`;
            } else if (msg.msg_type === 'audio') {
                contentHtml = `<audio controls class="max-w-[240px] h-10 mb-1"><source src="${msg.content}" type="audio/webm"></audio>`;
            } else {
                contentHtml = `<p class="break-words text-sm pr-6">${escapeHtml(msg.content)}</p>`;
            }

            let reactionsHtml = '';
            const reactions = msg.reactions || {};
            if (Object.keys(reactions).length > 0) {
                let rStr = '';
                for (let [user, emoji] of Object.entries(reactions)) {
                    rStr += `${emoji} `;
                }
                reactionsHtml = `<div class="absolute -bottom-2.5 right-2 bg-wa-panel border border-gray-700 rounded-full px-1.5 py-0.5 text-xs shadow">${rStr}</div>`;
            }

            div.innerHTML = `
                <div class="relative group p-2.5 rounded-xl ${isMe ? 'bg-wa-bubble text-white rounded-tr-none' : 'bg-wa-incoming text-gray-100 rounded-tl-none'} shadow">
                    ${!isMe && currentRecipient.startsWith('group_') ? `<div class="text-xs text-emerald-400 font-bold mb-1">${msg.sender}</div>` : ''}
                    ${contentHtml}
                    <div class="flex items-center justify-end space-x-1 text-[10px] text-gray-400 mt-1">
                        <span>${msg.timestamp}</span>
                        ${isMe ? '<i class="fa-solid fa-check-double text-blue-400"></i>' : ''}
                    </div>
                    ${reactionsHtml}
                    <!-- Reaction Popup Trigger -->
                    <div class="absolute top-1 right-1 hidden group-hover:flex bg-wa-panel/90 border border-gray-700 rounded-lg px-1 space-x-1 shadow">
                        <span class="cursor-pointer hover:scale-125 transition" onclick="sendReaction(${msg.id}, '❤️')">❤️</span>
                        <span class="cursor-pointer hover:scale-125 transition" onclick="sendReaction(${msg.id}, '👍')">👍</span>
                        <span class="cursor-pointer hover:scale-125 transition" onclick="sendReaction(${msg.id}, '😂')">😂</span>
                    </div>
                </div>
            `;
            
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        }

        function sendReaction(msgId, emoji) {
            ws.send(JSON.stringify({
                type: "reaction",
                msg_id: msgId,
                emoji: emoji,
                recipient: currentRecipient
            }));
        }

        function updateMessageReactions(msgId, reactions) {
            const msgEl = document.getElementById(`msg-${msgId}`);
            if (!msgEl) return;
            // Re-render reaction badge or reload conversation
            const bubble = msgEl.querySelector('.group');
            let existingBadge = bubble.querySelector('.absolute.-bottom-2\\.5');
            if (existingBadge) existingBadge.remove();

            let rStr = '';
            for (let [user, emoji] of Object.entries(reactions)) {
                rStr += `${emoji} `;
            }
            if (rStr) {
                const badge = document.createElement('div');
                badge.className = 'absolute -bottom-2.5 right-2 bg-wa-panel border border-gray-700 rounded-full px-1.5 py-0.5 text-xs shadow';
                badge.innerText = rStr;
                bubble.appendChild(badge);
            }
        }

        function openGroupModal() {
            const listContainer = document.getElementById("group-members-list");
            let html = '';
            onlineUsers.forEach(user => {
                html += `<label class="flex items-center space-x-2 text-sm cursor-pointer"><input type="checkbox" value="${user}" class="group-member-checkbox rounded bg-wa-dark border-gray-700 text-wa-green"><span>${user}</span></label>`;
            });
            listContainer.innerHTML = html;
            document.getElementById("group-modal").classList.remove("hidden");
        }

        function closeGroupModal() {
            document.getElementById("group-modal").classList.add("hidden");
        }

        async function createGroup() {
            const name = document.getElementById("group-name-input").value.trim();
            if (!name) return alert("Enter group name");

            const checkboxes = document.querySelectorAll(".group-member-checkbox:checked");
            const members = [currentUser, ...Array.from(checkboxes).map(cb => cb.value)];
            const group_id = "group_" + Date.now();

            await fetch('/api/groups', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({group_id, name, members})
            });

            closeGroupModal();
            fetchGroups();
            selectRecipient(group_id);
        }

        function toggleTheme() {
            const htmlEl = document.documentElement;
            htmlEl.classList.toggle('dark');
        }

        function startCall(type) {
            alert(`Initiating Metaverse ${type} call to ${currentRecipient}...`);
        }

        function handleKeyPress(e) {
            if (e.key === 'Enter') sendMessage();
        }

        function escapeHtml(text) {
            const map = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'};
            return text.replace(/[&<>"']/g, function(m) { return map[m]; });
        }
    </script>
</body>
</html>
    """

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
