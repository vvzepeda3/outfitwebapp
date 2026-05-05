import os
import uuid


from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
from functools import wraps


app = Flask(__name__)
app.secret_key = "replace_with_a_fixed_secret_in_production"


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB       = os.path.join(BASE_DIR, "users.db")


UPLOAD_FOLDER      = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}




# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn




def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT    UNIQUE NOT NULL,
                password TEXT    NOT NULL,
                campus   TEXT    NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                caption    TEXT,
                photo_path TEXT,
                links      TEXT,
                votes      INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS upvotes (
                user_id INTEGER NOT NULL,
                post_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, post_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (post_id) REFERENCES posts(id)
            )
        """)
        conn.commit()
    print("Database initialised. DB path:", DB)




# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper




def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )




# ─────────────────────────────────────────────────────────────────────────────
# Auth routes
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/", methods=["GET"])
def index():
    if "user_id" in session:
        return redirect(url_for("main_page"))
    return redirect(url_for("login"))




@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")


        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()


        if user and check_password_hash(user["password"], password):
            session["user_id"]  = user["id"]
            session["username"] = user["username"]
            session["campus"]   = user["campus"]
            return redirect(url_for("main_page"))
        else:
            return render_template("login.html", error="No account found with that username or password.")


    return render_template("login.html")




@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm",  "")
        campus   = request.form.get("campus",   "").strip()


        if not username or not password or not campus:
            flash("All fields are required.")
            return render_template("register.html")


        if password != confirm:
            flash("Passwords do not match.")
            return render_template("register.html")


        if len(password) < 8:
            flash("Password must be at least 8 characters.")
            return render_template("register.html")


        hashed = generate_password_hash(password)


        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO users (username, password, campus) VALUES (?, ?, ?)",
                    (username, hashed, campus)
                )
                conn.commit()
        except sqlite3.IntegrityError:
            flash("That username is already taken.")
            return render_template("register.html")


        flash("Account created! Please sign in.")
        return redirect(url_for("login"))


    return render_template("register.html")




@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))




# ─────────────────────────────────────────────────────────────────────────────
# Main page  (Discover / Profile / Leaderboard tabs)
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/home")
@login_required
def main_page():
    current_uid  = session["user_id"]
    profile_sort = request.args.get("sort", "newest")


    with get_db() as conn:


        # Discover feed
        posts = conn.execute("""
            SELECT p.*, u.username
            FROM posts p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.created_at DESC
            LIMIT 50
        """).fetchall()


        # Profile posts
        profile_order = (
            "ORDER BY votes DESC, created_at DESC"
            if profile_sort == "popular"
            else "ORDER BY created_at DESC"
        )
        profile_posts = conn.execute(
            f"SELECT * FROM posts WHERE user_id = ? {profile_order}",
            (current_uid,)
        ).fetchall()


        profile_stats = conn.execute("""
            SELECT COUNT(*) AS post_count,
                   COALESCE(SUM(votes), 0) AS total_votes
            FROM posts WHERE user_id = ?
        """, (current_uid,)).fetchone()


        # Leaderboard — top 3
        top3 = conn.execute("""
            SELECT p.*, u.username
            FROM posts p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.votes DESC, p.created_at DESC
            LIMIT 3
        """).fetchall()


        # Leaderboard — most recent
        recent = conn.execute("""
            SELECT p.*, u.username
            FROM posts p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.created_at DESC
            LIMIT 30
        """).fetchall()


        # Trend tracker
        trend = conn.execute("""
            SELECT p.*, u.username
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.links IS NOT NULL AND p.links != ''
            ORDER BY p.votes DESC, p.created_at DESC
            LIMIT 10
        """).fetchall()


        # Upvotes by current user
        upvoted_ids = set(
            row["post_id"] for row in conn.execute(
                "SELECT post_id FROM upvotes WHERE user_id = ?", (current_uid,)
            ).fetchall()
        )


    return render_template(
        "main.html",
        username        = session["username"],
        current_user_id = current_uid,
        posts           = posts,
        profile_posts   = profile_posts,
        profile_stats   = profile_stats,
        profile_sort    = profile_sort,
        top3            = top3,
        recent          = recent,
        trend           = trend,
        upvoted_ids     = upvoted_ids,
    )




# Alias so templates using url_for('main') still work
@app.route("/main")
def main():
    return redirect(url_for("main_page") if "user_id" in session else url_for("login"))




# ─────────────────────────────────────────────────────────────────────────────
# Upload a post
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/upload_post", methods=["POST"])
@login_required
def upload_post():
    caption = request.form.get("caption", "").strip()
    links   = request.form.get("links",   "").strip()
    photo   = request.files.get("photo")


    photo_path = None


    if photo and photo.filename and allowed_file(photo.filename):
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        ext      = secure_filename(photo.filename).rsplit(".", 1)[1].lower()
        filename = f"uploads/{uuid.uuid4().hex}.{ext}"
        photo.save(os.path.join(BASE_DIR, "static", filename))
        photo_path = filename


    with get_db() as conn:
        conn.execute(
            "INSERT INTO posts (user_id, caption, photo_path, links) VALUES (?, ?, ?, ?)",
            (session["user_id"], caption or None, photo_path, links or None)
        )
        conn.commit()


    flash("Your look has been posted!")
    return redirect(url_for("main_page") + "#profile")




# ─────────────────────────────────────────────────────────────────────────────
# Upvote (toggle)
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/upvote/<int:post_id>", methods=["POST"])
@login_required
def upvote(post_id):
    current_uid = session["user_id"]
    referrer    = request.referrer or url_for("main_page")


    with get_db() as conn:
        already = conn.execute(
            "SELECT 1 FROM upvotes WHERE user_id = ? AND post_id = ?",
            (current_uid, post_id)
        ).fetchone()


        if already:
            conn.execute(
                "DELETE FROM upvotes WHERE user_id = ? AND post_id = ?",
                (current_uid, post_id)
            )
            conn.execute(
                "UPDATE posts SET votes = MAX(0, votes - 1) WHERE id = ?",
                (post_id,)
            )
        else:
            conn.execute(
                "INSERT INTO upvotes (user_id, post_id) VALUES (?, ?)",
                (current_uid, post_id)
            )
            conn.execute(
                "UPDATE posts SET votes = votes + 1 WHERE id = ?",
                (post_id,)
            )
        conn.commit()


    return redirect(referrer)




# ─────────────────────────────────────────────────────────────────────────────
# Delete own post
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/delete_post/<int:post_id>", methods=["POST"])
@login_required
def delete_post(post_id):
    current_uid = session["user_id"]


    with get_db() as conn:
        post = conn.execute(
            "SELECT * FROM posts WHERE id = ? AND user_id = ?",
            (post_id, current_uid)
        ).fetchone()


        if post:
            if post["photo_path"]:
                full_path = os.path.join(BASE_DIR, "static", post["photo_path"])
                if os.path.exists(full_path):
                    os.remove(full_path)
            conn.execute("DELETE FROM upvotes WHERE post_id = ?", (post_id,))
            conn.execute("DELETE FROM posts    WHERE id = ?",     (post_id,))
            conn.commit()
        else:
            flash("Post not found or you don't have permission.")


    return redirect(url_for("main_page") + "#profile")




# ─────────────────────────────────────────────────────────────────────────────
# Public profile page  /profile/<uid>
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/profile/<int:uid>")
@login_required
def profile(uid):
    current_uid = session["user_id"]


    # Own profile — redirect to the profile tab on main page
    if uid == current_uid:
        return redirect(url_for("main_page") + "#profile")


    with get_db() as conn:
        owner = conn.execute(
            "SELECT id, username FROM users WHERE id = ?", (uid,)
        ).fetchone()


        if owner is None:
            flash("User not found.")
            return redirect(url_for("main_page"))


        sort = request.args.get("sort", "newest")
        order = "ORDER BY votes DESC, created_at DESC" if sort == "popular" else "ORDER BY created_at DESC"


        stats = conn.execute("""
            SELECT COUNT(*) AS post_count,
                   COALESCE(SUM(votes), 0) AS total_votes
            FROM posts WHERE user_id = ?
        """, (uid,)).fetchone()


        posts = conn.execute(
            f"SELECT * FROM posts WHERE user_id = ? {order}", (uid,)
        ).fetchall()


        upvoted_ids = set(
            row["post_id"] for row in conn.execute(
                "SELECT post_id FROM upvotes WHERE user_id = ?", (current_uid,)
            ).fetchall()
        )


    return render_template(
        "profile.html",
        owner          = owner,
        posts          = posts,
        stats          = stats,
        sort           = sort,
        upvoted_ids    = upvoted_ids,
        is_own_profile = False,
        current_uid    = current_uid,
    )




# ─────────────────────────────────────────────────────────────────────────────
# Leaderboard standalone page (used by leaderboard.html nav links)
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/leaderboard")
@login_required
def leaderboard():
    current_uid = session["user_id"]


    with get_db() as conn:
        top3 = conn.execute("""
            SELECT p.*, u.username
            FROM posts p JOIN users u ON p.user_id = u.id
            ORDER BY p.votes DESC, p.created_at DESC
            LIMIT 3
        """).fetchall()


        # "rest" = recent posts excluding top3
        top3_ids = tuple(p["id"] for p in top3) or (0,)
        placeholders = ",".join("?" * len(top3_ids))
        rest = conn.execute(f"""
            SELECT p.*, u.username
            FROM posts p JOIN users u ON p.user_id = u.id
            WHERE p.id NOT IN ({placeholders})
            ORDER BY p.created_at DESC
            LIMIT 30
        """, top3_ids).fetchall()


        trend = conn.execute("""
            SELECT p.*, u.username
            FROM posts p JOIN users u ON p.user_id = u.id
            WHERE p.links IS NOT NULL AND p.links != ''
            ORDER BY p.votes DESC, p.created_at DESC
            LIMIT 10
        """).fetchall()


        upvoted_ids = set(
            row["post_id"] for row in conn.execute(
                "SELECT post_id FROM upvotes WHERE user_id = ?", (current_uid,)
            ).fetchall()
        )


    return render_template(
        "leaderboard.html",
        top3        = top3,
        rest        = rest,
        trend       = trend,
        upvoted_ids = upvoted_ids,
    )




# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    init_db()
    app.run(debug=True)

