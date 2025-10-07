
# KEN Library App (Streamlit + SQLite) — minimal, safe, table-first

import sqlite3
from datetime import date
import pandas as pd
import streamlit as st

DB_PATH = "library.db"

# ------------------------- tiny DB helpers -------------------------
def get_conn():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("PRAGMA foreign_keys = ON")
    return con

def fetch_df(sql, params=()):
    with get_conn() as con:
        return pd.read_sql_query(sql, con, params=params)

def run_sql(sql, params=()):
    with get_conn() as con:
        cur = con.cursor()
        cur.execute(sql, params)
        con.commit()
        return cur.lastrowid

def _object_type(name: str) -> str | None:
    with sqlite3.connect(DB_PATH) as con:
        c = con.cursor()
        c.execute("SELECT type FROM sqlite_master WHERE name=?", (name,))
        row = c.fetchone()
        return row[0] if row else None

def _ensure_is_table(name: str):
    t = _object_type(name)
    if t and t != "table":
        with sqlite3.connect(DB_PATH) as con:
            c = con.cursor()
            c.execute(f"DROP {t.upper()} {name}")
            con.commit()

# ------------------------- schema + repair -------------------------
def init_db():
    with get_conn() as con:
        cur = con.cursor()

        # books
        cur.execute(\"\"\"
            CREATE TABLE IF NOT EXISTS books(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                author TEXT,
                genre TEXT,
                default_location TEXT,
                tags TEXT,
                notes TEXT
            )
        \"\"\")

        # members
        cur.execute(\"\"\"
            CREATE TABLE IF NOT EXISTS members(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                phone TEXT,
                email TEXT
            )
        \"\"\")

        # locations
        cur.execute(\"\"\"
            CREATE TABLE IF NOT EXISTS locations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                description TEXT
            )
        \"\"\")

        # ensure no conflicting object named "transactions"
        _ensure_is_table("transactions")

        # issue / return by BOOK (no copies)
        cur.execute(\"\"\"
            CREATE TABLE IF NOT EXISTS transactions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id     INTEGER,
                member_id   INTEGER,
                issue_date  TEXT DEFAULT DATE('now'),
                due_date    TEXT,
                return_date TEXT,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY(member_id) REFERENCES members(id) ON DELETE CASCADE
            )
        \"\"\")

        # helpful indexes
        cur.execute("CREATE INDEX IF NOT EXISTS ix_trans_open ON transactions(book_id) WHERE return_date IS NULL")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_trans_member ON transactions(member_id)")

        con.commit()

def ensure_default_locations(n: int = 45):
    \"\"\"Create Compartment 1..n if they don't exist (optional helper).\"\"\"
    with get_conn() as con:
        cur = con.cursor()
        cur.execute(\"\"\"
            CREATE TABLE IF NOT EXISTS locations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                description TEXT
            )
        \"\"\")
        for i in range(1, n + 1):
            cur.execute(
                "INSERT OR IGNORE INTO locations(name, description) VALUES(?, ?)",
                (f"Compartment {i}", f"Shelf compartment #{i}"),
            )
        con.commit()

# ------------------------------ UI helpers ------------------------------
def titled_metric(label: str, value):
    c1, c2 = st.columns([2,1])
    with c1: st.markdown(f"### {label}")
    with c2: st.markdown(f"## **{value}**")

def select_book(label="Book"):
    df = fetch_df("SELECT id, title, IFNULL(author,'') AS author FROM books ORDER BY title")
    if df.empty:
        st.info("No books yet. Import or add some on the **Books** page.")
        return None, None
    options = [f"{r.title} — {r.author}".strip(" —") for _, r in df.iterrows()]
    pick = st.selectbox(label, options)
    idx = options.index(pick)
    return int(df.iloc[idx]["id"]), df.iloc[idx]["title"]

def select_member(label="Member"):
    df = fetch_df("SELECT id, name FROM members ORDER BY name")
    if df.empty:
        st.info("No members yet. Import or add some on the **Members** page.")
        return None, None
    options = df["name"].tolist()
    pick = st.selectbox(label, options)
    idx = options.index(pick)
    return int(df.iloc[idx]["id"]), options[idx]

def select_location(label="Default Location", allow_empty=True):
    df = fetch_df("SELECT name FROM locations ORDER BY id")
    opts = df["name"].tolist()
    if allow_empty:
        opts = [""] + opts
    return st.selectbox(label, opts)

# ------------------------------ pages ------------------------------
def page_dashboard():
    st.title("KEN Library — Dashboard (tables only)")

    # KPIs
    total_books  = fetch_df("SELECT COUNT(*) AS c FROM books")[\"c\"][0]
    issued_now   = fetch_df("SELECT COUNT(*) AS c FROM transactions WHERE return_date IS NULL")[\"c\"][0]
    total_issues = fetch_df("SELECT COUNT(*) AS c FROM transactions")[\"c\"][0]

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Books", int(total_books))
    c2.metric("Issued Now (open)", int(issued_now))
    c3.metric("Total Issues Ever", int(total_issues))

    # Genres table
    st.subheader("Genres (table)")
    genre_tbl = fetch_df(\"\"\"
        SELECT
          COALESCE(b.genre,'(Uncategorized)') AS Genre,
          COUNT(*) AS Titles,
          SUM(
            EXISTS(SELECT 1 FROM transactions t
                   WHERE t.book_id=b.id AND t.return_date IS NULL)
          ) AS Titles_Issued_Now
        FROM books b
        GROUP BY b.genre
        ORDER BY Titles DESC, Genre
    \"\"\")
    st.dataframe(genre_tbl, use_container_width=True)

    # Click a genre to see its books
    st.markdown("### Pick a genre to list its books")
    genres = ["(All)"] + sorted(genre_tbl["Genre"].unique().tolist()) if not genre_tbl.empty else ["(All)"]
    pick = st.selectbox("Genre", genres, index=0)

    if pick == "(All)":
        df = fetch_df(\"\"\"
            SELECT id, title AS Title, author AS Author,
                   COALESCE(genre,'(Uncategorized)') AS Genre
            FROM books
            ORDER BY title
        \"\"\")
    else:
        df = fetch_df(\"\"\"
            SELECT id, title AS Title, author AS Author,
                   COALESCE(genre,'(Uncategorized)') AS Genre
            FROM books
            WHERE COALESCE(genre,'(Uncategorized)') = ?
            ORDER BY title
        \"\"\", (pick,))
    st.dataframe(df, use_container_width=True)

def page_issue_return():
    st.title("Issue / Return (no copies)")
    st.caption("Issue a book to a member. Later, mark it as returned. Due date is optional.")

    # Issue a book
    st.subheader("Issue a Book")
    book_id, book_title = select_book("Book")
    member_id, member_name = select_member("Member")
    due = st.date_input("Due date (optional)", value=None)

    can_issue = book_id is not None and member_id is not None
    if st.button("Issue", type="primary", disabled=not can_issue):
        run_sql(
            "INSERT INTO transactions(book_id, member_id, issue_date, due_date) VALUES (?, ?, DATE('now'), ?)",
            (book_id, member_id, str(due) if isinstance(due, date) else None)
        )
        st.success(f"Issued **{book_title}** to **{member_name}** ✅")

    st.divider()

    # Open issues table
    st.subheader("Open Issues")
    open_df = fetch_df(\"\"\"
        SELECT t.id AS Txn_ID,
               b.title AS Title,
               m.name  AS Member,
               t.issue_date AS Issued_On,
               t.due_date   AS Due_On
        FROM transactions t
        JOIN books b   ON b.id = t.book_id
        JOIN members m ON m.id = t.member_id
        WHERE t.return_date IS NULL
        ORDER BY t.issue_date DESC
    \"\"\")
    st.dataframe(open_df, use_container_width=True)

    if not open_df.empty:
        chosen = st.selectbox("Pick a transaction to mark returned", open_df["Txn_ID"].tolist())
        if st.button("Mark returned"):
            run_sql("UPDATE transactions SET return_date = DATE('now') WHERE id = ?", (int(chosen),))
            st.success("Marked as returned ✔️")

    st.divider()

    # History
    st.subheader("Recent Issue/Return History")
    hist_df = fetch_df(\"\"\"
        SELECT b.title AS Title,
               m.name  AS Member,
               t.issue_date AS Issued_On,
               t.due_date   AS Due_On,
               t.return_date AS Returned_On
        FROM transactions t
        JOIN books b   ON b.id = t.book_id
        JOIN members m ON m.id = t.member_id
        ORDER BY t.id DESC
        LIMIT 200
    \"\"\")
    st.dataframe(hist_df, use_container_width=True)

def page_books():
    st.title("Books")
    with st.expander("➕ Add a Book"):
        title = st.text_input("Title")
        author = st.text_input("Author")
        genre = st.text_input("Genre")
        default_loc = select_location("Default Location", allow_empty=True)
        if st.button("Add Book", type="primary") and title.strip():
            run_sql(\"\"\"
                INSERT INTO books(title, author, genre, default_location)
                VALUES(?, ?, ?, ?)
            \"\"\", (title.strip(), author.strip(), genre.strip(), default_loc))
            st.success("Book added.")

    df = fetch_df(\"\"\"
        SELECT id, title, author, genre, default_location
        FROM books
        ORDER BY title
    \"\"\")
    st.dataframe(df, use_container_width=True)

def page_members():
    st.title("Members")
    with st.expander("➕ Add a Member"):
        name = st.text_input("Name")
        phone = st.text_input("Phone")
        email = st.text_input("Email")
        if st.button("Add Member", type="primary") and name.strip():
            run_sql("INSERT INTO members(name, phone, email) VALUES(?, ?, ?)",
                    (name.strip(), phone.strip(), email.strip()))
            st.success("Member added.")

    df = fetch_df("SELECT id, name, phone, email FROM members ORDER BY name")
    st.dataframe(df, use_container_width=True)

def page_locations():
    st.title("Locations")
    with st.expander("➕ Add a Location"):
        name = st.text_input("Name")
        desc = st.text_input("Description")
        if st.button("Add Location", type="primary") and name.strip():
            run_sql("INSERT OR IGNORE INTO locations(name, description) VALUES(?, ?)",
                    (name.strip(), desc.strip()))
            st.success("Location added.")
    df = fetch_df("SELECT id, name, description FROM locations ORDER BY id")
    st.dataframe(df, use_container_width=True)

def page_import_export():
    st.title("Import / Export (CSV)")

    st.subheader("Repair / Initialize")
    if st.button("Run repair (recreate tables & 45 compartments)"):
        init_db()
        ensure_default_locations(45)
        st.success("Repair done.")

    # ---- Import Books ----
    st.subheader("Import Books (CSV)")
    st.caption("Expected columns: title, author, genre, default_location")
    up_books = st.file_uploader("Upload books.csv", type=["csv"], key="books_csv")
    if up_books is not None:
        df = pd.read_csv(up_books)
        needed = {"title","author","genre","default_location"}
        cols = {c.lower(): c for c in df.columns}
        if not needed.issubset(set(cols.keys())):
            st.error("Books CSV missing required columns.")
        else:
            df = df.rename(columns={cols.get(c, c): c for c in cols})
            rows = []
            for _, r in df.iterrows():
                rows.append((
                    str(r.get("title","")).strip(),
                    str(r.get("author","")).strip(),
                    str(r.get("genre","")).strip(),
                    str(r.get("default_location","")).strip(),
                ))
            with get_conn() as con:
                cur = con.cursor()
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_books_title_author ON books(title, author)")
                cur.executemany(\"\"\"
                    INSERT OR IGNORE INTO books(title, author, genre, default_location)
                    VALUES(?, ?, ?, ?)
                \"\"\", rows)
                con.commit()
            st.success(f"Imported {len(rows)} books (existing titles skipped).")

    # ---- Import Members ----
    st.subheader("Import Members (CSV)")
    st.caption("Expected columns: name, phone, email")
    up_members = st.file_uploader("Upload members.csv", type=["csv"], key="members_csv")
    if up_members is not None:
        df = pd.read_csv(up_members)
        needed = {"name","phone","email"}
        cols = {c.lower(): c for c in df.columns}
        if not needed.issubset(set(cols.keys())):
            st.error("Members CSV missing required columns.")
        else:
            df = df.rename(columns={cols.get(c, c): c for c in cols})
            rows = []
            for _, r in df.iterrows():
                rows.append((
                    str(r.get("name","")).strip(),
                    str(r.get("phone","")).strip(),
                    str(r.get("email","")).strip(),
                ))
            with get_conn() as con:
                cur = con.cursor()
                cur.executemany(\"\"\"
                    INSERT INTO members(name, phone, email) VALUES(?, ?, ?)
                \"\"\", rows)
                con.commit()
            st.success(f"Imported {len(rows)} members.")

    # ---- Import Locations ----
    st.subheader("Import Locations (CSV)")
    st.caption("Expected columns: name, description")
    up_locations = st.file_uploader("Upload locations.csv", type=["csv"], key="locations_csv")
    if up_locations is not None:
        df = pd.read_csv(up_locations)
        needed = {"name","description"}
        cols = {c.lower(): c for c in df.columns}
        if not needed.issubset(set(cols.keys())):
            st.error("Locations CSV missing required columns.")
        else:
            df = df.rename(columns={cols.get(c, c): c for c in cols})
            rows = []
            for _, r in df.iterrows():
                rows.append((
                    str(r.get("name","")).strip(),
                    str(r.get("description","")).strip(),
                ))
            with get_conn() as con:
                cur = con.cursor()
                cur.executemany(\"\"\"
                    INSERT OR IGNORE INTO locations(name, description) VALUES(?, ?)
                \"\"\", rows)
                con.commit()
            st.success(f"Imported {len(rows)} locations (duplicates ignored).")

    st.subheader("Export CSV")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Export Books"):
            df = fetch_df("SELECT * FROM books ORDER BY title")
            st.download_button("Download books.csv", df.to_csv(index=False).encode("utf-8"),
                               "books.csv", "text/csv")
    with c2:
        if st.button("Export Members"):
            df = fetch_df("SELECT * FROM members ORDER BY name")
            st.download_button("Download members.csv", df.to_csv(index=False).encode("utf-8"),
                               "members.csv", "text/csv")
    with c3:
        if st.button("Export Locations"):
            df = fetch_df("SELECT * FROM locations ORDER BY id")
            st.download_button("Download locations.csv", df.to_csv(index=False).encode("utf-8"),
                               "locations.csv", "text/csv")

# ------------------------------ main ------------------------------
def main():
    st.set_page_config(page_title="KEN Library", page_icon="📚", layout="wide")

    # Create/repair DB + seed locations (optional)
    init_db()
    ensure_default_locations(45)

    with st.sidebar:
        st.markdown("## Go to")
        page = st.radio("", [
            "Dashboard", "Issue / Return", "Books", "Members", "Locations", "Import / Export"
        ], index=0)

    pages = {
        "Dashboard": page_dashboard,
        "Issue / Return": page_issue_return,
        "Books": page_books,
        "Members": page_members,
        "Locations": page_locations,
        "Import / Export": page_import_export,
    }
    pages[page]()

if __name__ == "__main__":
    main()
