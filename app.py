import streamlit as st
import sqlite3

# Page config with a WhatsApp-style title and icon
st.set_page_config(page_title="Metaverse WhatsApp", page_icon="🟢", layout="wide")

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
    conn.commit()
    return conn

db_conn = init_db()
cursor = db_conn.cursor()

# ==================== AUTHENTICATION / LOGIN ====================
if "user" not in st.session_state:
    st.session_state.user = ""

if not st.session_state.user:
    st.title("⚡ Metaverse WhatsApp - Streamlit Edition")
    st.markdown("Enter your username or email to access your secure chat node:")
    
    username_input = st.text_input("Username / Email", placeholder="e.g., alex@gmail.com")
    if st.button("Login to Node"):
        if username_input.strip():
            st.session_state.user = username_input.strip()
            st.rerun()
        else:
            st.error("Please enter a valid username.")
    st.stop()

# ==================== SIDEBAR & CONTACTS ====================
currentUser = st.session_state.user
st.sidebar.title(f"⚡ Node: {currentUser}")

if st.sidebar.button("Logout"):
    st.session_state.user = ""
    st.rerun()

st.sidebar.divider()
st.sidebar.subheader("Saved Contacts")

# Fetch contacts from database history
cursor.execute("""
    SELECT DISTINCT sender FROM messages WHERE recipient = ?
    UNION
    SELECT DISTINCT recipient FROM messages WHERE sender = ?
""", (currentUser, currentUser))
rows = cursor.fetchall()
saved_contacts = [r[0] for r in rows if r[0] and r[0] != currentUser]

# Ensure Brian AI is always available
if "Brian 🧠 (AI Archive)" not in saved_contacts:
    saved_contacts.insert(0, "Brian 🧠 (AI Archive)")

# Add new contact option
new_contact = st.sidebar.text_input("Start chat with (Email):", placeholder="contact@gmail.com")
if st.sidebar.button("Open Chat") and new_contact:
    if new_contact not in saved_contacts and new_contact != currentUser:
        saved_contacts.append(new_contact)
    st.session_state.active_contact = new_contact
    st.rerun()

# Set active contact default
if "active_contact" not in st.session_state:
    st.session_state.active_contact = saved_contacts[0] if saved_contacts else "Brian 🧠 (AI Archive)"

selected_contact = st.sidebar.radio("Select Chat:", saved_contacts, index=saved_contacts.index(st.session_state.active_contact) if st.session_state.active_contact in saved_contacts else 0)
st.session_state.active_contact = selected_contact
active_contact = st.session_state.active_contact

st.sidebar.divider()
if st.sidebar.button("🗑️ Clear Active Chat"):
    cursor.execute("""
        DELETE FROM messages 
        WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
    """, (currentUser, active_contact, active_contact, currentUser))
    db_conn.commit()
    st.sidebar.success("Chat history cleared!")
    st.rerun()

# ==================== CHAT PANEL ====================
st.title(f"💬 Chat: {active_contact}")

# Fetch chat history between current user and active contact
cursor.execute("""
    SELECT id, sender, type, content FROM messages 
    WHERE (sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?)
    ORDER BY id ASC
""", (currentUser, active_contact, active_contact, currentUser))
history = cursor.fetchall()

# Display Messages
for msg_id, sender, msg_type, content in history:
    is_me = (sender == currentUser)
    with st.chat_message("user" if is_me else "assistant"):
        if msg_type == "chat":
            st.write(content)
        elif msg_type == "image":
            st.image(content, width=300)
            
        # Delete individual message button
        if st.button("❌ Delete", key=f"del_{msg_id}"):
            cursor.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
            db_conn.commit()
            st.rerun()

# Chat Input Box
prompt = st.chat_input("Type a secure message...")
if prompt:
    # Save user message
    cursor.execute("INSERT INTO messages (sender, recipient, type, content) VALUES (?, ?, ?, ?)", 
                   (currentUser, active_contact, "chat", prompt))
    db_conn.commit()

    # Automated response if chatting with Brian AI
    if active_contact == "Brian 🧠 (AI Archive)":
        brian_reply = f"🧠 [Brian Vault Archive]: Safely indexed your note -> '{prompt}'"
        cursor.execute("INSERT INTO messages (sender, recipient, type, content) VALUES (?, ?, ?, ?)", 
                       ("Brian 🧠 (AI Archive)", currentUser, "chat", brian_reply))
        db_conn.commit()

    st.rerun()
