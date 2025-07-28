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
    # PATCH BURADA!
    sql = sql.replace("≥", ">=").replace("≤", "<=")
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

    openai_api_key = st.secrets["OPENAI_API_KEY"]
    filter_applied = False
    if not df.empty:
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
                        filter_applied = True
                    except Exception as e:
                        st.error(f"Query failed: {e}")
        else:
            st.success("Daha fazla filtre önerilmiyor. Arama tamamlandı.")

    st.dataframe(df.head(100))
    
    if not df.empty:
        car_labels = df['brand'].astype(str) + " " + df['model'].astype(str) + " (" + df['year'].astype(str) + ")"
        selected = st.selectbox(
            "Tahmini motor hacmi & 0-100 hızlanması görmek için aracı seç:",
            car_labels
        )
        if selected:
            sel_idx = car_labels[car_labels == selected].index[0]
            row = df.loc[sel_idx]
            car_desc = f"{row['brand']} {row['model']} {row['year']}"
            prompt = (
                f"Aşağıdaki otomobil için tahmini teknik verileri özetle, sadece gerçekçi ortalama değerlerle yanıtla:\n"
                f"Marka ve model: {car_desc}\n"
                f"- Motor hacmi (cc)\n"
                f"- 0-100 km/s hızlanma süresi (sn)\n"
                f"Sadece kısa bir tablo olarak yaz. Eğer bilgi yoksa 'Bilinmiyor' yaz."
            )
            client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
            with st.spinner("Tahmini teknik veriler çekiliyor..."):
                resp = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}]
                )
                gpt_out = resp.choices[0].message.content.strip()
            st.markdown("**Tahmini teknik veriler (AI):**")
            st.info(gpt_out)
    else:
        st.warning("Hiçbir araba bulunamadı.")
        last_sql = st.session_state["last_sql"]
        brand = None
        model = None
        brand_match = re.search(r"brand ILIKE '%([^']+)%'", last_sql, re.IGNORECASE)
        model_match = re.search(r"model ILIKE '%([^']+)%'", last_sql, re.IGNORECASE)
        if brand_match:
            brand = brand_match.group(1)
        if model_match:
            model = model_match.group(1)
        if brand:
            engine = get_engine()
            similar_models_query = f"""
                SELECT DISTINCT model 
                FROM cars
                WHERE brand ILIKE '%{brand}%'
                ORDER BY model;
            """
            similar_models = pd.read_sql(similar_models_query, engine)
            if not similar_models.empty:
                st.info(f"{brand.upper()} için mevcut modeller:")
                st.write(", ".join(similar_models['model'].astype(str).tolist()))
            else:
                st.info(f"{brand.upper()} için başka model bulunamadı.")
        else:
            st.info("Benzer marka/model bulunamadı.")

if st.button("Tüm filtreleri sıfırla"):
    st.session_state["query_history"] = []
    st.session_state["last_sql"] = None
    st.session_state["last_df"] = None
    st.rerun()
