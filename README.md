# AutoBot — AI-Powered Car Search (Turkish, Streamlit + OpenAI)

This is an AI-powered car search app written in Python/Streamlit, designed for Turkish users.\
The codebase and interface are entirely in Turkish, and it leverages OpenAI's GPT (with Vision support) to let users search for cars by writing queries or uploading car images.

---

##  Try it yourself!

The app is live at:\
👉 [**https://autobot-becho.streamlit.app/**](https://autobot-becho.streamlit.app/)

---

## Features

- **Text-based Search:**\
  Enter a Turkish query (e.g. “2018 sonrası dizel BMW”), and the app generates a safe SQL query with AI.\
  Further refine with AI-suggested filters.

- **Photo Search:**\
  Upload a car photo — AI (OpenAI Vision) detects the brand/model, then searches your database for matches or similar cars.

- **Case-insensitive, fuzzy search** (ILIKE + wildcards).

- **Self-healing:**\
  If there are no results, shows similar models for the given brand.

- **Secure login/register/anonymous access** (hashed passwords).

---

## Dataset

This app uses the following Kaggle dataset:

- [Used Car Dataset - Ford and Mercedes by adityadesai13](https://www.kaggle.com/datasets/adityadesai13/used-car-dataset-ford-and-mercedes)

**Note:**\
Various errors, inconsistencies, and missing values in the original dataset have been fixed before using it in this app.\
Some column names, values, or types may have been standardized or corrected to ensure reliable querying and better AI performance.

### Fixes applied to the original dataset

- Cleaned up missing or invalid values
- Standardized text case for string columns (e.g. brand, model)
- Unified fuel type labels (e.g. "petrol" instead of "Gasoline" or "Benzin")
- Fixed inconsistent column naming
- Removed obvious outliers or duplicates

---

## Example Table Schema

```sql
CREATE TABLE cars (
  id SERIAL PRIMARY KEY,
  brand TEXT,
  model TEXT,
  year INTEGER,
  transmission TEXT,
  mileage INTEGER,
  fueltype TEXT,
  price INTEGER,
  source_file TEXT
);
```

---

## Usage

1. **Install requirements:**

   ```bash
   pip install streamlit openai psycopg2 pandas
   ```

2. **Environment:**\
   Place your OpenAI API key and PostgreSQL DB URL in `.streamlit/secrets.toml`:

   ```toml
   OPENAI_API_KEY = "sk-..."          # Your OpenAI API Key
   NEON_DB_URL = "postgresql://user:pass@host:port/db"
   ```

3. **Run the app:**

   ```bash
   streamlit run streamlit_app.py
   ```

4. **Browse to the app** and choose either:

   - **Yazılı Sorgu (Filtrelerle):**  Enter Turkish queries and filter step by step.
   - **Fotoğraftan Bul (Görsel Analiz):**  Upload a car photo for visual search.

---

## Notes

- The entire interface and all prompts are in Turkish.
- If you wish to adapt this for English or another language, you’ll need to localize both the UI and prompt logic.
- OpenAI Vision features require GPT-4o or gpt-4-vision-preview access.
- For production: keep your `.streamlit/secrets.toml` **private** and never commit it to public repos.

---

## Author

- Becho-PNG

---

*Questions? Suggestions? Need an English version? Open an issue.
