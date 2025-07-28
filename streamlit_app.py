import streamlit as st
import psycopg2
import os
from openai import OpenAI
import re
import hashlib
import uuid
import pandas as pd

@st.cache_resource
def get_connection():
    return psycopg2.connect(st.secrets["NEON_DB_URL"])

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
            conn = get_connection()
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
        else:  # Login
            if not username or not password:
                st.error("Fill all fields")
                return
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id, password FROM users WHERE username = %s;",
                (username,)
            )
            row = cur.fetchone()
            cur.close()
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
        "When filtering text columns (like brand, model, transmission, fueltype, source_file), always use ILIKE for case-insensitive matches instead of = or LIKE."
        "If the query doesn't specify a limit, use 'LIMIT 100' at the end."
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
    return sql

def run_sql(sql):
    conn = get_connection()
    try:
        df = pd.read_sql(sql, conn)
    except Exception as e:
        raise e
    return df

def gpt_generate_followup(history, df, schema_hint, openai_api_key):
    client = OpenAI(api_key=openai_api_key)
    # 5 örnekten fazlasını GPT'ye vermeye gerek yok
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
    login_form()
    st.stop()

if "query_history" not in st.session_state:
    st.session_state["query_history"] = []
if "last_sql" not in st.session_state:
    st.session_state["last_sql"] = None
if "last_df" not in st.session_state:
    st.session_state["last_df"] = None

top_left, top_right = st.columns([0.85, 0.15])
with top_right:
    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()

st.title("AutoBot - Adım Adım Akıllı Araba Arama 🚗🤖")
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

st.markdown(
    "Sorgunu yaz: `10k'dan yüksek BMW'ler`, `2018 sonrası dizel Audi`, vs. Sonra AI sana ek filtre soracak. Her adımda daha fazla filtreleyebilirsin!"
)

with st.form("first_search", clear_on_submit=True):
    user_query = st.text_input("Araba sorgusu gir:", key="main_query")
    submitted = st.form_submit_button("Ara")

if submitted and user_query:
    st.session_state["query_history"] = [user_query]
    st.session_state["last_sql"] = None
    st.session_state["last_df"] = None
    openai_api_key = st.secrets["OPENAI_API_KEY"]
    with st.spinner("GPT sorgu oluşturuyor..."):
        try:
            sql = gpt_generate_sql(st.session_state["query_history"], schema_hint, openai_api_key)
            st.session_state["last_sql"] = sql
            df = run_sql(sql)
            st.session_state["last_df"] = df
        except Exception as e:
            st.error(f"Query failed: {e}")

if st.session_state["last_df"] is not None:
    st.code(st.session_state["last_sql"], language="sql")
    df = st.session_state["last_df"]
    if df.empty:
        st.warning("Hiçbir araba bulunamadı.")
    else:
        st.dataframe(df.head(100))
        # GPT'den follow-up question iste
        openai_api_key = st.secrets["OPENAI_API_KEY"]
        with st.spinner("Daha akıllı filtre önerisi hazırlanıyor..."):
            followup = gpt_generate_followup(
                st.session_state["query_history"], df, schema_hint, openai_api_key
            )
        if followup != "Daha fazla filtrelemeye gerek yok.":
            st.markdown(f"**AI'nin filtre sorusu:** {followup}")
            with st.form("filter_step", clear_on_submit=True):
                user_filter = st.text_input("Ek filtrele:", key="next_filter")
                filter_submitted = st.form_submit_button("Filtrele")
            if filter_submitted and user_filter:
                st.session_state["query_history"].append(user_filter)
                with st.spinner("Yeni filtreyle arama yapılıyor..."):
                    try:
                        sql = gpt_generate_sql(st.session_state["query_history"], schema_hint, openai_api_key)
                        st.session_state["last_sql"] = sql
                        df = run_sql(sql)
                        st.session_state["last_df"] = df
                    except Exception as e:
                        st.error(f"Query failed: {e}")
        else:
            st.success("Daha fazla filtre önerilmiyor. Arama tamamlandı.")

if st.button("Tüm filtreleri sıfırla"):
    st.session_state["query_history"] = []
    st.session_state["last_sql"] = None
    st.session_state["last_df"] = None
    st.rerun()
