import os
import json
import psycopg2
import streamlit as st
from openai import OpenAI
import uuid
import base64
import hashlib
import pandas as pd
import re

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
    last_msgs = history[-5:]
    example_lines = "\n".join(f"- {msg['content']}" for msg in last_msgs)
    return f"""This is a returning user. Here are recent things they have said:
{example_lines}
When responding, consider the user's style and topics above.
"""

def image_to_base64(img_bytes):
    return base64.b64encode(img_bytes).decode("utf-8")

# --- 🔥 CAR SEARCH/COMPARE HELPERS ---

def query_cars(brand=None, model=None, max_price=None, n=5):
    conn = psycopg2.connect(st.secrets["NEON_DB_URL"])
    sql = "SELECT * FROM cars WHERE TRUE"
    params = []
    if brand:
        sql += " AND LOWER(brand) LIKE %s"
        params.append(f"%{brand.lower()}%")
    if model:
        sql += " AND LOWER(model) LIKE %s"
        params.append(f"%{model.lower()}%")
    if max_price:
        sql += " AND price <= %s"
        params.append(max_price)
    sql += f" LIMIT {n}"
    df = pd.read_sql(sql, conn, params=params)
    conn.close()
    return df

def compare_cars(brand1, model1, brand2, model2):
    df1 = query_cars(brand1, model1, n=1)
    df2 = query_cars(brand2, model2, n=1)
    if df1.empty or df2.empty:
        return None
    comp = pd.concat([df1, df2], keys=[f"{brand1} {model1}", f"{brand2} {model2}"])
    return comp

def handle_user_query(message):
    brands = ['ford', 'audi', 'bmw', 'mercedes', 'toyota', 'skoda', 'hyundai']
    brand = None
    model = None
    price = None
    mileage = None
    year = None

    # Parse brand
    for b in brands:
        if b in message.lower():
            brand = b
            break

    # Price
    price_match = re.search(r'(?:under|below|less than)? ?(\d{2,7}) ?₺?', message.replace(",", ""))
    if price_match:
        price = int(price_match.group(1))

    # Mileage
    mileage_match = re.search(r'(?:under|below|less than)? ?(\d{2,7}) ?km', message.replace(",", ""))
    if mileage_match:
        mileage = int(mileage_match.group(1))

    # Year
    year_match = re.search(r'(?:from|after|since)? ?(20\d{2}|19\d{2})', message)
    if year_match:
        year = int(year_match.group(1))

    # Model
    if brand:
        rest = message.lower().split(brand)[-1].strip()
        model_guess = rest.split()[0] if rest.split() else None
        if model_guess and not model_guess.isnumeric():
            model = model_guess

    df = query_cars(brand=brand, model=model, max_price=price, max_mileage=mileage, min_year=year)
    if df.empty:
        return "No cars found matching your query.", None
    else:
        summary = f"Found {len(df)} cars"
        if brand: summary += f" for {brand.title()}"
        if model: summary += f" {model.title()}"
        if price: summary += f" under {price}₺"
        if mileage: summary += f" with mileage under {mileage}km"
        if year: summary += f" from {year} or newer"
        summary += ". Here are the first few:"
        return summary, df.head(5)

    # Otherwise, basic car search
    # Try to parse brand, model, max_price
    brand = model = None
    price = None
    brands = ['ford', 'audi', 'bmw', 'mercedes', 'toyota', 'skoda', 'hyundai']
    for b in brands:
        if b in message.lower():
            brand = b
            break
    price_match = re.search(r'(\d{2,7}) ?₺?', message.replace(",", ""))
    if price_match:
        price = int(price_match.group(1))
    if brand:
        rest = message.lower().split(brand)[-1].strip()
        model_guess = rest.split()[0] if rest.split() else None
        if model_guess and not model_guess.isnumeric():
            model = model_guess

    def query_cars(brand=None, model=None, max_price=None, max_mileage=None, min_year=None, n=5):
    conn = psycopg2.connect(st.secrets["NEON_DB_URL"])
    sql = "SELECT * FROM cars WHERE TRUE"
    params = []
    if brand:
        sql += " AND LOWER(brand) LIKE %s"
        params.append(f"%{brand.lower()}%")
    if model:
        sql += " AND LOWER(model) LIKE %s"
        params.append(f"%{model.lower()}%")
    if max_price:
        sql += " AND price <= %s"
        params.append(max_price)
    if max_mileage:
        sql += " AND mileage <= %s"
        params.append(max_mileage)
    if min_year:
        sql += " AND year >= %s"
        params.append(min_year)
    sql += f" ORDER BY price ASC LIMIT {n}"
    df = pd.read_sql(sql, conn, params=params)
    conn.close()
    return df
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

    st.title("AutoBot - Car Dealership Chat")
    st.write(f"👤 Logged in as: `{st.session_state.get('user', '')}`")
    st.write(f"💬 Session ID: `{st.session_state['session_id']}`")

    if "last_uploaded" not in st.session_state:
        st.session_state.last_uploaded = None

    uploaded_image = st.file_uploader(
        "Upload an image(png, jpg, jpeg)",
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

    # Show messages
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

    if prompt := st.chat_input("Ask AutoBot for a car, or compare two cars (e.g. 'Compare BMW 320i and Audi A4', 'Show me Toyotas under 400000')"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # 🔥 Special logic: car search or comparison
        answer, cars = handle_user_query(prompt)
        with st.chat_message("assistant"):
            st.markdown(answer)
            if cars is not None and not cars.empty:
                st.dataframe(cars)

        st.session_state.messages.append({"role": "assistant", "content": answer})
        save_chat_history(
            st.session_state["user_id"],
            st.session_state["session_id"],
            st.session_state.messages,
        )
