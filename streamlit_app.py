import streamlit as st
from sqlalchemy import create_engine, text
from openai import OpenAI
import re
import hashlib
import uuid
import pandas as pd

@st.cache_resource
def get_engine():
    return create_engine(st.secrets["NEON_DB_URL"])

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def login_form():
    st.title("Login / Register / Anonymous")
    choice = st.radio("Choose action", ["Login", "Register", "Continue Anonymously"])
    username = password = ""
    if choice in ["Login", "Register"]:
        username = st.text_input("Username").strip().lower()
        password = st.text_input("Password", type="password")
    if st.button(choice):
        engine = get_engine()
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
            with engine.connect() as conn:
                trans = conn.begin()
                try:
                    res = conn.execute(
                        text("INSERT INTO users (username, password) VALUES (:username, :password) RETURNING user_id;"),
                        {"username": username, "password": hash_password(password)}
                    )
                    user_id = res.scalar()
                    trans.commit()
                    st.success("Registered! Please log in.")
                except Exception as e:
                    trans.rollback()
                    if "unique" in str(e).lower():
                        st.error("Username already exists.")
                    else:
                        st.error(f"Registration failed: {e}")
        else:
            if not username or not password:
                st.error("Fill all fields")
                return
            with engine.connect() as conn:
                res = conn.execute(
                    text("SELECT user_id, password FROM users WHERE username = :username;"),
                    {"username": username}
                )
                row = res.fetchone()
                if row and row[1] == hash_password(password):
                    st.session_state["user"] = username
                    st.session_state["user_id"] = row[0]
                    st.success(f"Welcome {username}")
                    st.rerun()
                else:
                    st.error("Invalid login.")

def gpt_generate_sql(history, schema_hint, openai_api_key):
    client = OpenAI(api_key=openai_api_key)
    full_query = " ".join(history)
    system_prompt = (
        f"You are an expert at writing SQL queries for this table:\n"
        f"{schema_hint}\n"
        "Always return a valid SQL SELECT query using only the 'cars' table, and never use DROP, DELETE, INSERT, UPDATE, or any non-SELECT commands."
        "If a column is not specified by the user, leave it unfiltered."
        "When filtering text columns (like brand, model, transmission, fueltype, source_file), always use ILIKE with wildcards (e.g. %BMW%) for case-insensitive and partial matches, not just ILIKE 'value'."
        "If the query doesn't specify a limit, use 'LIMIT 100' at the end."
        "Return only a single SQL SELECT statement."
    )
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_query},
        ]
    )
    sql_raw = resp.choices[0].message.content.strip()
    sql_match = re.search(r"(SELECT[\s\S]+?;)", sql_raw, re.IGNORECASE)
    if sql_match:
        sql = sql_match.group(1)
    else:
        sqls = re.findall(r"select.+", sql_raw, re.IGNORECASE | re.DOTALL)
        if not sqls:
            raise ValueError("No SELECT query found in GPT output.")
        sql = sqls[0].strip()
    if not sql.lower().startswith("select"):
        raise ValueError("GPT returned a non-SELECT query. Blocked for safety.")
    if "limit" not in sql.lower():
        sql = sql.rstrip(";") + " LIMIT 100;"
    sql = re.sub(r'\bLIKE\b', 'ILIKE', sql, flags=re.IGNORECASE)
    sql = re.sub(r"model\s+ILIKE\s+'([^']+)'", r"model ILIKE '%\1%'", sql, flags=re.IGNORECASE)
    sql = re.sub(r"brand\s+ILIKE\s+'([^']+)'", r"brand ILIKE '%\1%'", sql, flags=re.IGNORECASE)
    matches = re.findall(r"SELECT[\s\S]+?;", sql, re.IGNORECASE)
    if matches:
        sql = matches[0]
    else:
        raise ValueError("No valid SELECT statement in GPT SQL output!")
    # Unicode büyük-eşittir/küçük-eşittir karakterlerini düzelt
    sql = sql.replace("≥", ">=").replace("≤", "<=")
    # Birden fazla % işaretini normalize et
    sql = re.sub(r"%{2,}", "%", sql)
    return sql

def run_sql(sql):
    db_url = st.secrets["NEON_DB_URL"]
    try:
        if not isinstance(sql, str):
            raise ValueError("SQL query must be a string!")
        df = pd.read_sql(sql, db_url)
    except Exception as e:
        st.error(f"Çalıştırılan SQL: {sql}")
        raise e
    return df

def gpt_generate_followup(history, df, schema_hint, openai_api_key):
    client = OpenAI(api_key=openai_api_key)
    df_head = df.head(5).to_dict(orient="records") if not df.empty else []
    prompt = (
        f"Given this search context: {' '.join(history)}\n"
        f"Result sample: {df_head}\n"
        f"Table schema: {schema_hint}\n"
        "Suggest one follow-up question (in Turkish) to further filter or narrow the search. Be context-aware. "
        "If there is no more useful filtering to ask, just say 'Daha fazla filtrelemeye gerek yok.'"
    )
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ]
    )
    followup = resp.choices[0].message.content.strip()
    return followup

if "user_id" not in st.session_state:
    login_form()_
