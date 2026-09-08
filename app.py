import os
import sqlite3
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="Metaverse WhatsApp")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "chat.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            profile_pic TEXT,
            status TEXT DEFAULT 'Hey there! I am using Metaverse WhatsApp.'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            receiver TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Auto-seed sample contacts if empty
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("""
            INSERT INTO users (name, email, profile_pic, status) VALUES (?, ?, ?, ?)
        """, [
            ("Alex Rivera", "alex@metaverse.io", "https://api.dicebear.com/7.x/bottts/svg?seed=Alex", "Coding in the Metaverse 🚀"),
            ("Sara Khan", "sara@metaverse.io", "https://api.dicebear.com/7.x/bottts/svg?seed=Sara", "Busy | In a meeting"),
            ("Metaverse Bot", "bot@metaverse.io", "https://api.dicebear.com/7.x/bottts/svg?seed=Bot", "24/7 AI Assistant online")
        ])
    conn.commit()
    conn.close()

init_db()

# --- BACKEND ENDPOINTS ---

@app.get("/api/contacts")
async def get_contacts(current_user_email: str = Query("", description="Current user email to exclude")):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, email, profile_pic, status FROM users WHERE email != ?",
            (current_user_email,)
        )
        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "id": r[0],
                "name": r[1] or r[2].split('@')[0],
                "email": r[2],
                "avatar": r[3] or f"https://api.dicebear.com/7.x/bottts/svg?seed={r[2]}",
                "status": r[4] or "Hey there! I am using Metaverse WhatsApp."
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/messages")
async def get_messages(user1: str, user2: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sender, receiver, content, timestamp FROM messages
        WHERE (sender = ? AND receiver = ?) OR (sender = ? AND receiver = ?)
        ORDER BY timestamp ASC
    """, (user1, user2, user2, user1))
    rows = cursor.fetchall()
    conn.close()
    return [{"sender": r[0], "receiver": r[1], "content": r[2], "timestamp": r[3]} for r in rows]

# --- WEBSOCKET CONNECTION MANAGER ---

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]

    async def send_direct_message(self, message: str, receiver_id: str):
        if receiver_id in self.active_connections:
            await self.active_connections[receiver_id].send_text(message)

manager = ConnectionManager()

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket, client_id)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.send_direct_message(data, client_id)
    except WebSocketDisconnect:
        manager.disconnect(client_id)

# --- METAVERSE WHATSAPP UI ---

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Metaverse WhatsApp</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
            body { background: #0b141a; color: #e9edef; height: 100vh; display: flex; justify-content: center; align-items: center; }
            .app-wrapper { display: flex; width: 95vw; height: 92vh; background: #111b21; border-radius: 12px; overflow: hidden; border: 1px solid rgba(255, 255, 255, 0.08); box-shadow: 0 20px 50px rgba(0,0,0,0.5); }
            
            /* Sidebar */
            .sidebar { width: 360px; background: #111b21; border-right: 1px solid #222d34; display: flex; flex-direction: column; }
            .sidebar-header { height: 60px; background: #202c33; padding: 10px 16px; display: flex; align-items: center; justify-content: space-between; }
            .user-avatar { width: 40px; height: 40px; border-radius: 50%; background: #00a884; }
            .search-box { padding: 8px 12px; background: #111b21; border-bottom: 1px solid #222d34; }
            .search-input { width: 100%; padding: 8px 12px; background: #202c33; border: none; border-radius: 8px; color: #e9edef; outline: none; }
            .contacts-list { flex: 1; overflow-y: auto; }
            .contact-item { display: flex; align-items: center; padding: 12px 16px; gap: 12px; cursor: pointer; border-bottom: 1px solid #1f2c34; transition: 0.15s; }
            .contact-item:hover { background: #202c33; }
            .contact-avatar { width: 48px; height: 48px; border-radius: 50%; }
            .contact-details { flex: 1; min-width: 0; }
            .contact-name { font-weight: 600; font-size: 0.95rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
            .contact-status { font-size: 0.8rem; color: #8696a0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

            /* Chat Area */
            .chat-container { flex: 1; display: flex; flex-direction: column; background: #0b141a; }
            .chat-header { height: 60px; background: #202c33; padding: 10px 16px; display: flex; align-items: center; justify-content: space-between; border-left: 1px solid #222d34; }
            .chat-header-info { display: flex; align-items: center; gap: 12px; }
            .chat-messages { flex: 1; padding: 20px; overflow-y: auto; background-image: radial-gradient(rgba(255,255,255,0.03) 1px, transparent 0); background-size: 24px 24px; display: flex; flex-direction: column; gap: 12px; }
            .message-bubble { max-width: 60%; padding: 8px 12px; border-radius: 8px; font-size: 0.9rem; line-height: 1.4; word-wrap: break-word; }
            .sent { background: #005c4b; align-self: flex-end; border-top-right-radius: 0; }
            .received { background: #202c33; align-self: flex-start; border-top-left-radius: 0; }
            
            /* Input Bar */
            .chat-input-area { height: 62px; background: #202c33; padding: 10px 16px; display: flex; align-items: center; gap: 12px; }
            .chat-input { flex: 1; padding: 10px 14px; background: #2a3942; border: none; border-radius: 8px; color: #e9edef; font-size: 0.95rem; outline: none; }
            .send-btn { background: #00a884; border: none; color: #fff; width: 40px; height: 40px; border-radius: 50%; cursor: pointer; display: flex; justify-content: center; align-items: center; }

            /* Context Menu */
            .context-menu { position: fixed; background: #233138; border-radius: 8px; border: 1px solid #2a3942; box-shadow: 0 10px 25px rgba(0,0,0,0.5); display: none; z-index: 1000; min-width: 140px; }
            .context-menu-item { padding: 10px 16px; font-size: 0.85rem; color: #e9edef; cursor: pointer; }
            .context-menu-item:hover { background: #182229; }
        </style>
    </head>
    <body>
        <div class="app-wrapper">
            <div class="sidebar">
                <div class="sidebar-header">
                    <img src="https://api.dicebear.com/7.x/bottts/svg?seed=Me" class="user-avatar" id="my-avatar" />
                    <span style="font-weight:600; font-size: 0.9rem;">Metaverse WhatsApp</span>
                </div>
                <div class="search-box">
                    <input type="text" class="search-input" placeholder="Search or start new chat" id="search-input" onkeyup="filterContacts()" />
                </div>
                <div class="contacts-list" id="contacts-list"></div>
            </div>

            <div class="chat-container">
                <div class="chat-header">
                    <div class="chat-header-info" id="chat-header-info">
                        <span style="opacity: 0.7;">Select a conversation to start messaging</span>
                    </div>
                </div>
                <div class="chat-messages" id="chat-messages"></div>
                <div class="chat-input-area">
                    <input type="text" class="chat-input" id="message-input" placeholder="Type a message..." onkeypress="handleKeyPress(event)" />
                    <button class="send-btn" onclick="sendMessage()">➤</button>
                </div>
            </div>
        </div>

        <div class="context-menu" id="context-menu">
            <div class="context-menu-item" onclick="copyMessage()">Copy Text</div>
            <div class="context-menu-item" onclick="deleteMessage()">Delete Message</div>
        </div>

        <script>
            const currentUser = "me@metaverse.io";
            let activeContact = null;
            let contactsData = [];

            async function fetchContacts() {
                const list = document.getElementById('contacts-list');
                try {
                    const res = await fetch(`/api/contacts?current_user_email=${encodeURIComponent(currentUser)}`);
                    contactsData = await res.json();
                    renderContacts(contactsData);
                } catch (e) {
                    list.innerHTML = `<div style="padding:16px; opacity:0.6; text-align:center;">Failed to load contacts.</div>`;
                }
            }

            function renderContacts(data) {
                const list = document.getElementById('contacts-list');
                if (!data || data.length === 0) {
                    list.innerHTML = `<div style="padding:16px; opacity:0.6; text-align:center;">No contacts found</div>`;
                    return;
                }
                list.innerHTML = data.map(c => `
                    <div class="contact-item" onclick="selectContact('${c.email}', '${c.name}', '${c.avatar}')">
                        <img src="${c.avatar}" class="contact-avatar" />
                        <div class="contact-details">
                            <div class="contact-name">${c.name}</div>
                            <div class="contact-status">${c.status}</div>
                        </div>
                    </div>
                `).join('');
            }

            function filterContacts() {
                const query = document.getElementById('search-input').value.toLowerCase();
                const filtered = contactsData.filter(c => c.name.toLowerCase().includes(query) || c.status.toLowerCase().includes(query));
                renderContacts(filtered);
            }

            function selectContact(email, name, avatar) {
                activeContact = { email, name, avatar };
                document.getElementById('chat-header-info').innerHTML = `
                    <img src="${avatar}" style="width:36px; height:36px; border-radius:50%;" />
                    <div>
                        <div style="font-weight:600;">${name}</div>
                        <div style="font-size:0.75rem; color:#8696a0;">Online</div>
                    </div>
                `;
                loadChatHistory();
            }

            async function loadChatHistory() {
                if (!activeContact) return;
                const msgs = document.getElementById('chat-messages');
                try {
                    const res = await fetch(`/api/messages?user1=${encodeURIComponent(currentUser)}&user2=${encodeURIComponent(activeContact.email)}`);
                    const data = await res.json();
                    msgs.innerHTML = data.map(m => `
                        <div class="message-bubble ${m.sender === currentUser ? 'sent' : 'received'}" oncontextmenu="showContextMenu(event, this)">
                            ${m.content}
                        </div>
                    `).join('');
                    msgs.scrollTop = msgs.scrollHeight;
                } catch(e) {
                    console.error("Failed to fetch history:", e);
                }
            }

            function sendMessage() {
                const input = document.getElementById('message-input');
                const text = input.value.trim();
                if (!text || !activeContact) return;

                const msgs = document.getElementById('chat-messages');
                msgs.innerHTML += `<div class="message-bubble sent" oncontextmenu="showContextMenu(event, this)">${text}</div>`;
                input.value = '';
                msgs.scrollTop = msgs.scrollHeight;
            }

            function handleKeyPress(e) {
                if (e.key === 'Enter') sendMessage();
            }

            let selectedElement = null;
            function showContextMenu(e, el) {
                e.preventDefault();
                selectedElement = el;
                const menu = document.getElementById('context-menu');
                menu.style.top = `${e.clientY}px`;
                menu.style.left = `${e.clientX}px`;
                menu.style.display = 'block';
            }

            document.addEventListener('click', () => {
                document.getElementById('context-menu').style.display = 'none';
            });

            function copyMessage() {
                if (selectedElement) navigator.clipboard.writeText(selectedElement.innerText);
            }

            function deleteMessage() {
                if (selectedElement) selectedElement.remove();
            }

            window.onload = () => {
                fetchContacts();
            };
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
