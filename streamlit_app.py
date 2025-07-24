import os
import json
import psycopg2
import streamlit as st
from openai import OpenAI
import uuid
import base64

# Auth functions (move these to auth.py if you want, or keep here)
import hashlib

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def login_form():
    st.title("Login / Register / Anonymous")
    choice = st.radio("Choose action", ["Login", "Register", "Continue Anonymously"])
    username = password = ""
    if choice in ["Login", "Register"]:
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
    if st.button(choice):
        if choice == "Continue Anonymously":
            anon_id = "anon-" + str(uuid.uuid4())[:12]
            st.session_state["user_id"] = anon_id
            st.session_state["user"] = "Anonymous"
            st.success(f"Continuing anonymously. Your session id: {anon_id}")
            st.rerun()
        elif choice == "Register":
            if not username or not password:
                st.error("Fill all fields")
                return
            conn = psycopg2.connect(st.secrets["NEON_DB_URL"])
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO users (username, password) VALUES (%s, %s) RETURNING user_id;",
                    (username, hash_password(password))
                )
                user_id = cur.fetchone()[0]
                conn.commit()
                st.success("Registered! Please log in.")
            except psycopg2.errors.UniqueViolation:
                st.error("Username already exists.")
                conn.rollback()
            finally:
                cur.close()
                conn.close()
        else:  # Login
            if not username or not password:
                st.error("Fill all fields")
                return
            conn = psycopg2.connect(st.secrets["NEON_DB_URL"])
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id, password FROM users WHERE username = %s;",
                (username,)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row and row[1] == hash_password(password):
                st.session_state["user"] = username
                st.session_state["user_id"] = row[0]
                st.success(f"Welcome {username}")
                st.rerun()
            else:
                st.error("Invalid login.")
# ---- Chat/Database Functions ----

def get_chat_history(user_id, session_id):
    conn = psycopg2.connect(st.secrets["NEON_DB_URL"])
    cur = conn.cursor()
    cur.execute(
        "SELECT messages FROM chat_logs WHERE user_id = %s AND session_id = %s",
        (user_id, session_id),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else []

def save_chat_history(user_id, session_id, messages):
    conn = psycopg2.connect(st.secrets["NEON_DB_URL"])
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO chat_logs (user_id, session_id, messages, updated_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, session_id)
        DO UPDATE SET messages = EXCLUDED.messages, updated_at = CURRENT_TIMESTAMP;
        """,
        (user_id, session_id, json.dumps(messages)),
    )
    conn.commit()
    cur.close()
    conn.close()

def list_sessions(user_id):
    conn = psycopg2.connect(st.secrets["NEON_DB_URL"])
    cur = conn.cursor()
    cur.execute(
        "SELECT session_id, updated_at FROM chat_logs WHERE user_id = %s ORDER BY updated_at DESC",
        (user_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def fetch_all_user_history(user_id):
    conn = psycopg2.connect(st.secrets["NEON_DB_URL"])
    cur = conn.cursor()
    cur.execute(
        "SELECT messages FROM chat_logs WHERE user_id = %s ORDER BY updated_at",
        (user_id,)
    )
    all_msgs = []
    for row in cur.fetchall():
        all_msgs.extend([msg for msg in row[0] if msg["role"] == "user"])
    cur.close()
    conn.close()
    return all_msgs

def get_user_persona_prompt(user_id):
    history = fetch_all_user_history(user_id)
    last_msgs = history[-5:]  # limit for brevity
    example_lines = "\n".join(f"- {msg['content']}" for msg in last_msgs)
    return f"""This is a returning user. Here are recent things they have said:
{example_lines}
When responding, consider the user's style and topics above.
"""

def image_to_base64(img_bytes):
    return base64.b64encode(img_bytes).decode("utf-8")

# ---- Streamlit App Start ----

if "user_id" not in st.session_state:
    login_form()
    st.stop()

if "active_page" not in st.session_state:
    st.session_state.active_page = "select_session"

client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# SESSION SELECTION PAGE
if st.session_state.active_page == "select_session":
    top_left, top_right = st.columns([0.85, 0.15])
    with top_right:
        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()
    st.title("Select Conversation")
    sessions = list_sessions(st.session_state["user_id"])

    if not sessions:
        st.info("No previous sessions found.")
        if st.button("Start New Conversation"):
            session_id = str(uuid.uuid4())[:8]
            st.session_state.session_id = session_id
            st.session_state.messages = []
            st.session_state.active_page = "chat"
            st.rerun()
    else:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Start New Conversation"):
                session_id = str(uuid.uuid4())[:8]
                st.session_state.session_id = session_id
                st.session_state.messages = []
                st.session_state.active_page = "chat"
                st.rerun()
        with col2:
            session_labels = [f"{s[0]} (Last: {s[1].strftime('%Y-%m-%d %H:%M:%S')})" for s in sessions]
            selected = st.selectbox("Select previous session", session_labels)
            if st.button("Go to Selected Session"):
                idx = session_labels.index(selected)
                session_id = sessions[idx][0]
                st.session_state.session_id = session_id
                st.session_state.messages = get_chat_history(st.session_state["user_id"], session_id)
                st.session_state.active_page = "chat"
                st.rerun()

# CHAT PAGE
if st.session_state.get("active_page") == "chat":
    top_left, top_right = st.columns([0.85, 0.15])
    with top_right:
        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()
        if st.button("Back to Sessions"):
            st.session_state.active_page = "select_session"
            st.rerun()

    st.title("Chatbot")
    st.write(f"👤 Logged in as: `{st.session_state.get('user', '')}`")
    st.write(f"💬 Session ID: `{st.session_state['session_id']}`")

    # Track last uploaded image to prevent spamming
    if "last_uploaded" not in st.session_state:
        st.session_state.last_uploaded = None

    uploaded_image = st.file_uploader(
        "Upload an image for GPT-4o to analyze (png, jpg, jpeg)",
        type=["png", "jpg", "jpeg"], key="img-uploader"
    )

    if uploaded_image is not None and uploaded_image != st.session_state.last_uploaded:
        img_bytes = uploaded_image.read()
        img_b64 = image_to_base64(img_bytes)
        st.session_state.messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": "Please analyze this image."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
            ]
        })
        with st.chat_message("user"):
            st.markdown("Uploaded image:")
            st.image(img_bytes)
        st.session_state.last_uploaded = uploaded_image
        st.rerun()

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if isinstance(message["content"], list):
                for c in message["content"]:
                    if c["type"] == "text":
                        st.markdown(c["text"])
                    elif c["type"] == "image_url":
                        st.image(c["image_url"]["url"])
            else:
                st.markdown(message["content"])

    if prompt := st.chat_input("What is up?"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        persona_prompt = get_user_persona_prompt(st.session_state["user_id"])
        MAX_HISTORY = 15
        recent_msgs = st.session_state.messages[-MAX_HISTORY:]

        mm_msgs = [{"role": "system", "content": persona_prompt}]
        for msg in recent_msgs:
            if isinstance(msg["content"], list):
                mm_msgs.append({"role": msg["role"], "content": msg["content"]})
            else:
                mm_msgs.append({"role": msg["role"], "content": msg["content"]})

        with st.chat_message("assistant"):
            stream = client.chat.completions.create(
                model="gpt-4o",
                messages=mm_msgs,
                stream=True,
            )
            response = st.write_stream(stream)

        st.session_state.messages.append({"role": "assistant", "content": response})
        save_chat_history(
            st.session_state["user_id"],
            st.session_state["session_id"],
            st.session_state.messages,
        )
