import streamlit as st
import psycopg2
import os
from openai import OpenAI
import re

# Auth (if needed)
import hashlib
import uuid

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

# --- LLM-to-SQL logic ---
def gpt_generate_sql(user_query, schema_hint, openai_api_key):
    client = OpenAI(api_key=openai_api_key)
    system_prompt = (
        f"You are an expert at writing SQL queries for this table:\n"
        f"{schema_hint}\n"
        "Always return a valid SQL SELECT query using only the 'cars' table, and never use DROP, DELETE, INSERT, UPDATE, or any non-SELECT commands."
        "If a column is not specified by the user, leave it unfiltered."
    )
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ]
    )
    sql = resp.choices[0].message.content.strip()
    # Remove any markdown code block ticks etc.
    sql = re.sub(r"^```(sql)?", "", sql, flags=re.MULTILINE).replace("```", "").strip()
    # Only allow SELECT statements for safety
    if not sql.lower().startswith("select"):
        raise ValueError("GPT returned a non-SELECT query. Blocked for safety.")
    return sql

def run_sql(sql):
    conn = psycopg2.connect(st.secrets["NEON_DB_URL"])
    import pandas as pd
    try:
        df = pd.read_sql(sql, conn)
    except Exception as e:
        conn.close()
        raise e
    conn.close()
    return df

# --- Streamlit App ---
if "user_id" not in st.session_state:
    login_form()
    st.stop()

top_left, top_right = st.columns([0.85, 0.15])
with top_right:
    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()

st.title("AutoBot - Car Search by AI")
st.write(f"👤 Logged in as: `{st.session_state.get('user', '')}`")

schema_hint = """
Table name: cars
Columns:
- brand (text)
- model (text)
- year (integer)
- transmission (text)
- mileage (integer)
- fueltype (text)
- price (integer)
- source_file (text)
"""

st.markdown("Ask me to search cars! Example: `Show me all BMWs under 500000 with less than 100000 km after 2018`.")

user_query = st.text_input("What kind of car do you want to search?")

if user_query:
    openai_api_key = st.secrets["OPENAI_API_KEY"]
    try:
        sql = gpt_generate_sql(user_query, schema_hint, openai_api_key)
        st.code(sql, language="sql")
        df = run_sql(sql)
        if df.empty:
            st.warning("No cars found matching your criteria.")
        else:
            st.dataframe(df)
    except Exception as e:
        st.error(f"Query failed: {e}")
