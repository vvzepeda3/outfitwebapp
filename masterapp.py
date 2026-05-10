import os
import uuid

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import psycopg2
import psycopg2.extras
from functools import wraps
import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key    = os.environ.get("CLOUDINARY_API_KEY"),
    api_secret = os.environ.get("CLOUDINARY_API_SECRET"),
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "replace_with_a_fixed_secret_in_production")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.environ.get("DATABASE_URL")

UPLOAD_FOLDER      = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "heic"}

UC_SCHOOLS = [
    { "id": "berkeley", "name": "UC Berkeley",     "short": "CAL",  "logo": "https://upload.wikimedia.org/wikipedia/commons/a/a1/Seal_of_University_of_California%2C_Berkeley.svg" },
    { "id": "ucla",     "name": "UC Los Angeles",  "short": "UCLA", "logo": "https://upload.wikimedia.org/wikipedia/commons/0/0d/The_University_of_California_UCLA.svg" },
    { "id": "ucsd",     "name": "UC San Diego",    "short": "UCSD", "logo": "https://upload.wikimedia.org/wikipedia/commons/c/c1/Seal_of_the_University_of_California%2C_San_Diego.svg" },
    { "id": "ucsb",     "name": "UC Santa Barbara","short": "UCSB", "logo": "https://upload.wikimedia.org/wikipedia/commons/4/48/UC_Santa_Barbara_Seal.png" },
    { "id": "uci",      "name": "UC Irvine",       "short": "UCI",  "logo": "https://upload.wikimedia.org/wikipedia/commons/b/b0/The_University_of_California_Irvine.svg" },
    { "id": "ucd",      "name": "UC Davis",        "short": "UCD",  "logo": "https://upload.wikimedia.org/wikipedia/commons/f/f3/The_University_of_California_Davis.svg" },
    { "id": "ucsc",     "name": "UC Santa Cruz",   "short": "UCSC", "logo": "https://upload.wikimedia.org/wikipedia/commons/5/53/The_University_of_California_1868_UCSC.svg" },
    { "id": "ucr",      "name": "UC Riverside",    "short": "UCR",  "logo": "https://upload.wikimedia.org/wikipedia/en/5/51/UC_Riverside_seal.svg" },
    { "id": "ucm",      "name": "UC Merced",       "short": "UCM",  "logo": "https://upload.wikimedia.org/wikipedia/en/5/51/UC_Merced_Seal.png" },
]

CAMPUS_TO_ID = {
    "UC Berkeley":     "berkeley",
    "UC Los Angeles":  "ucla",
    "UC San Diego":    "ucsd",
    "UC Santa Barbara":"ucsb",
    "UC Irvine":       "uci",
    "UC Davis":        "ucd",
    "UC Santa Cruz":   "ucsc",
    "UC Riverside":    "ucr",
    "UC Merced":       "ucm",
}

# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id       SERIAL PRIMARY KEY,
                    username TEXT   UNIQUE NOT NULL,
                    password TEXT   NOT NULL,
                    campus   TEXT   NOT NULL DEFAULT ''
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id         SERIAL PRIMARY KEY,
                    user_id    INTEGER  NOT NULL,
                    caption    TEXT,
                    photo_path TEXT,
                    links      TEXT,
                    votes      INTEGER  DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS upvotes (
                    user_id INTEGER NOT NULL,
                    post_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, post_id),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (post_id) REFERENCES posts(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS follows (
                    follower_id INTEGER NOT NULL,
                    followed_id INTEGER NOT NULL,
                    PRIMARY KEY (follower_id, followed_id),
                    FOREIGN KEY (follower_id) REFERENCES users(id),
                    FOREIGN KEY (followed_id) REFERENCES users(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id      TEXT    PRIMARY KEY,
                    post_id INTEGER NOT NULL,
                    name    TEXT,
                    brand   TEXT,
                    FOREIGN KEY (post_id) REFERENCES posts(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS closet (
                    user_id INTEGER NOT NULL,
                    item_id TEXT    NOT NULL,
                    PRIMARY KEY (user_id, item_id),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (item_id) REFERENCES items(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS likes (
                    user_id INTEGER NOT NULL,
                    post_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, post_id),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (post_id) REFERENCES posts(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS link_clicks (
                    id         SERIAL PRIMARY KEY,
                    post_id    INTEGER   NOT NULL,
                    url        TEXT      NOT NULL,
                    clicked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (post_id) REFERENCES posts(id)
                )
            """)
        conn.commit()
    print("Database initialised.")


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
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def attach_items_to_posts(conn, posts_list):
    formatted = []
    for post in posts_list:
        post_dict = dict(post)
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, brand FROM items WHERE post_id = %s", (post["id"],))
            db_items = cur.fetchall()
        post_dict["items"]    = [{"id": i["id"], "name": i["name"], "brand": i["brand"]} for i in db_items]
        post_dict["imageUrl"] = post["photo_path"] if post["photo_path"] else ""
        post_dict["userName"] = post.get("username", "")
        if post_dict.get("links"):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT url, COUNT(*) as cnt FROM link_clicks WHERE post_id = %s GROUP BY url",
                    (post["id"],)
                )
                click_rows = cur.fetchall()
            post_dict["link_clicks"] = {row["url"]: row["cnt"] for row in click_rows}
        else:
            post_dict["link_clicks"] = {}

        formatted.append(post_dict)
    return formatted


# ─────────────────────────────────────────────────────────────────────────────
# Auth routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    if "user_id" in session:
        return redirect(url_for("home"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            user = cur.fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"]  = user["id"]
            session["username"] = user["username"]
            session["campus"]   = user["campus"]
            return redirect(url_for("home"))
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

        hashed = generate_password_hash(password, method="pbkdf2:sha256")

        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (username, password, campus) VALUES (%s, %s, %s)",
                    (username, hashed, campus)
                )
            conn.commit()
            conn.close()
        except psycopg2.errors.UniqueViolation:
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
# Home page
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/home")
@login_required
def home():
    current_uid  = session["user_id"]
    user_campus  = session.get("campus", "")
    campus_id    = CAMPUS_TO_ID.get(user_campus, "")
    campus_short = next((s["short"] for s in UC_SCHOOLS if s["id"] == campus_id), user_campus)

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT followed_id FROM follows WHERE follower_id = %s", (current_uid,))
        followed_ids = [row["followed_id"] for row in cur.fetchall()]

        following_posts = []
        if followed_ids:
            cur.execute("""
                SELECT p.*, u.username FROM posts p
                JOIN users u ON p.user_id = u.id
                WHERE p.user_id = ANY(%s)
                ORDER BY p.created_at DESC LIMIT 30
            """, (followed_ids,))
            following_posts = attach_items_to_posts(conn, cur.fetchall())

        campus_posts = []
        if user_campus:
            cur.execute("""
                SELECT p.*, u.username FROM posts p
                JOIN users u ON p.user_id = u.id
                WHERE u.campus = %s
                ORDER BY p.votes DESC, p.created_at DESC LIMIT 12
            """, (user_campus,))
            campus_posts = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("SELECT item_id FROM closet WHERE user_id = %s", (current_uid,))
        saved_items = [row["item_id"] for row in cur.fetchall()]

        cur.execute("SELECT post_id FROM likes WHERE user_id = %s", (current_uid,))
        liked_ids = set(row["post_id"] for row in cur.fetchall())

    conn.close()

    return render_template(
        "home.html",
        username        = session["username"],
        current_user_id = current_uid,
        user_campus     = user_campus,
        campus_short    = campus_short,
        following_posts = following_posts,
        campus_posts    = campus_posts,
        saved_items     = saved_items,
        following_set   = set(followed_ids),
        schools         = UC_SCHOOLS,
        liked_ids       = liked_ids,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Discover page
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/discover")
@login_required
def discover():
    current_uid = session["user_id"]
    school_id   = request.args.get("school", "berkeley")
    school      = next((s for s in UC_SCHOOLS if s["id"] == school_id), UC_SCHOOLS[0])
    campus_name = school["name"]

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.*, u.username FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE u.campus = %s
            ORDER BY p.votes DESC, p.created_at DESC LIMIT 50
        """, (campus_name,))
        posts = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("SELECT item_id FROM closet WHERE user_id = %s", (current_uid,))
        saved_items = [row["item_id"] for row in cur.fetchall()]

        cur.execute("SELECT followed_id FROM follows WHERE follower_id = %s", (current_uid,))
        followed_ids = set(row["followed_id"] for row in cur.fetchall())

        cur.execute("SELECT post_id FROM likes WHERE user_id = %s", (current_uid,))
        liked_ids = set(row["post_id"] for row in cur.fetchall())

    conn.close()

    return render_template(
        "discover.html",
        username        = session["username"],
        current_user_id = current_uid,
        schools         = UC_SCHOOLS,
        active_school   = school,
        posts           = posts,
        saved_items     = saved_items,
        followed_ids    = list(followed_ids),
        liked_ids       = list(liked_ids),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main page (Profile tab)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/main")
@login_required
def main_page():
    current_uid  = session["user_id"]
    profile_sort = request.args.get("sort", "newest")
    profile_order = (
        "ORDER BY votes DESC, created_at DESC"
        if profile_sort == "popular"
        else "ORDER BY created_at DESC"
    )

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT p.*, u.username FROM posts p JOIN users u ON p.user_id = u.id WHERE p.user_id = %s {profile_order}",
            (current_uid,)
        )
        profile_posts = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("""
            SELECT COUNT(*) AS post_count, COALESCE(SUM(votes), 0) AS total_votes
            FROM posts WHERE user_id = %s
        """, (current_uid,))
        profile_stats = cur.fetchone()

        cur.execute("""
            SELECT p.*, u.username FROM posts p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.votes DESC, p.created_at DESC LIMIT 3
        """)
        top3 = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("""
            SELECT p.*, u.username FROM posts p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.created_at DESC LIMIT 30
        """)
        recent = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("""
            SELECT p.*, u.username FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.links IS NOT NULL AND p.links != ''
            ORDER BY p.votes DESC, p.created_at DESC LIMIT 10
        """)
        trend = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("SELECT post_id FROM upvotes WHERE user_id = %s", (current_uid,))
        upvoted_ids = set(row["post_id"] for row in cur.fetchall())

        cur.execute("SELECT item_id FROM closet WHERE user_id = %s", (current_uid,))
        saved_items = [row["item_id"] for row in cur.fetchall()]

        cur.execute("""
            SELECT p.*, u.username FROM posts p
            JOIN users u ON p.user_id = u.id
            JOIN likes l ON l.post_id = p.id
            WHERE l.user_id = %s
            ORDER BY p.created_at DESC
        """, (current_uid,))
        liked_posts = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("SELECT post_id FROM likes WHERE user_id = %s", (current_uid,))
        liked_ids = set(row["post_id"] for row in cur.fetchall())

        cur.execute("""
            SELECT post_id, url, COUNT(*) as cnt FROM link_clicks
            WHERE post_id IN (SELECT id FROM posts WHERE user_id = %s)
            GROUP BY post_id, url
        """, (current_uid,))
        own_click_totals = {}
        for row in cur.fetchall():
            own_click_totals.setdefault(row["post_id"], {})[row["url"]] = row["cnt"]

    conn.close()

    return render_template(
        "main.html",
        username         = session["username"],
        campus           = session.get("campus", ""),
        current_user_id  = current_uid,
        profile_posts    = profile_posts,
        profile_stats    = profile_stats,
        profile_sort     = profile_sort,
        top3             = top3,
        recent           = recent,
        trend            = trend,
        upvoted_ids      = upvoted_ids,
        schools          = UC_SCHOOLS,
        saved_items      = saved_items,
        liked_posts      = liked_posts,
        liked_ids        = liked_ids,
        own_click_totals = own_click_totals,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Leaderboard
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/leaderboard")
@login_required
def leaderboard():
    current_uid = session["user_id"]

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.*, u.username FROM posts p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.votes DESC, p.created_at DESC LIMIT 3
        """)
        top3 = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("""
            SELECT p.*, u.username FROM posts p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.created_at DESC LIMIT 30
        """)
        rest = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("""
            SELECT p.*, u.username FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.links IS NOT NULL AND p.links != ''
            ORDER BY p.votes DESC, p.created_at DESC LIMIT 10
        """)
        trend = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("SELECT post_id FROM upvotes WHERE user_id = %s", (current_uid,))
        upvoted_ids = set(row["post_id"] for row in cur.fetchall())

    conn.close()

    return render_template(
        "leaderboard.html",
        username    = session["username"],
        top3        = top3,
        rest        = rest,
        trend       = trend,
        upvoted_ids = upvoted_ids,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Profile routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/profile")
@login_required
def profile_self():
    uid = session["user_id"]
    sort  = request.args.get("sort", "newest")
    order = "ORDER BY votes DESC, created_at DESC" if sort == "popular" else "ORDER BY created_at DESC"

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id, username, campus FROM users WHERE id = %s", (uid,))
        owner = cur.fetchone()

        cur.execute("""
            SELECT COUNT(*) AS post_count, COALESCE(SUM(votes), 0) AS total_votes
            FROM posts WHERE user_id = %s
        """, (uid,))
        stats = cur.fetchone()

        cur.execute(
            f"SELECT p.*, u.username FROM posts p JOIN users u ON p.user_id = u.id WHERE p.user_id = %s {order}",
            (uid,)
        )
        posts = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("SELECT post_id FROM upvotes WHERE user_id = %s", (uid,))
        upvoted_ids = set(row["post_id"] for row in cur.fetchall())

        cur.execute("""
            SELECT p.*, u.username FROM posts p
            JOIN users u ON p.user_id = u.id
            JOIN likes l ON l.post_id = p.id
            WHERE l.user_id = %s ORDER BY p.created_at DESC
        """, (uid,))
        liked_posts = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("SELECT post_id FROM likes WHERE user_id = %s", (uid,))
        liked_ids = set(row["post_id"] for row in cur.fetchall())

        cur.execute("""
            SELECT post_id, url, COUNT(*) as cnt FROM link_clicks
            WHERE post_id IN (SELECT id FROM posts WHERE user_id = %s)
            GROUP BY post_id, url
        """, (uid,))
        click_totals = {}
        for row in cur.fetchall():
            click_totals.setdefault(row["post_id"], {})[row["url"]] = row["cnt"]

    conn.close()

    return render_template(
        "profile.html",
        owner            = owner,
        posts            = posts,
        stats            = stats,
        sort             = sort,
        upvoted_ids      = upvoted_ids,
        liked_posts      = liked_posts,
        liked_ids        = liked_ids,
        is_own_profile   = True,
        is_following     = False,
        current_uid      = uid,
        current_username = session["username"],
        click_totals     = click_totals,
    )


@app.route("/profile/<int:uid>")
@login_required
def profile(uid):
    current_uid = session["user_id"]
    if uid == current_uid:
        return redirect(url_for("profile_self"))

    sort  = request.args.get("sort", "newest")
    order = "ORDER BY votes DESC, created_at DESC" if sort == "popular" else "ORDER BY created_at DESC"

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id, username, campus FROM users WHERE id = %s", (uid,))
        owner = cur.fetchone()

        if owner is None:
            conn.close()
            flash("User not found.")
            return redirect(url_for("home"))

        cur.execute("""
            SELECT COUNT(*) AS post_count, COALESCE(SUM(votes), 0) AS total_votes
            FROM posts WHERE user_id = %s
        """, (uid,))
        stats = cur.fetchone()

        cur.execute(
            f"SELECT p.*, u.username FROM posts p JOIN users u ON p.user_id = u.id WHERE p.user_id = %s {order}",
            (uid,)
        )
        posts = attach_items_to_posts(conn, cur.fetchall())

        cur.execute("SELECT post_id FROM upvotes WHERE user_id = %s", (current_uid,))
        upvoted_ids = set(row["post_id"] for row in cur.fetchall())

        cur.execute("""
            SELECT 1 FROM follows WHERE follower_id = %s AND followed_id = %s
        """, (current_uid, uid))
        is_following = bool(cur.fetchone())

        cur.execute("""
            SELECT post_id, url, COUNT(*) as cnt FROM link_clicks
            WHERE post_id IN (SELECT id FROM posts WHERE user_id = %s)
            GROUP BY post_id, url
        """, (uid,))
        click_totals = {}
        for row in cur.fetchall():
            click_totals.setdefault(row["post_id"], {})[row["url"]] = row["cnt"]

    conn.close()

    return render_template(
        "profile.html",
        owner            = owner,
        posts            = posts,
        stats            = stats,
        sort             = sort,
        upvoted_ids      = upvoted_ids,
        liked_posts      = [],
        liked_ids        = set(),
        is_own_profile   = False,
        is_following     = is_following,
        current_uid      = current_uid,
        current_username = session["username"],
        click_totals     = click_totals,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Follow / Unfollow
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/follow/<int:uid>", methods=["POST"])
@login_required
def follow(uid):
    current_uid = session["user_id"]
    if uid == current_uid:
        return jsonify({"status": "error", "message": "Cannot follow yourself"}), 400

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM follows WHERE follower_id = %s AND followed_id = %s", (current_uid, uid))
        existing = cur.fetchone()
        if existing:
            cur.execute("DELETE FROM follows WHERE follower_id = %s AND followed_id = %s", (current_uid, uid))
            status = "unfollowed"
        else:
            cur.execute("INSERT INTO follows (follower_id, followed_id) VALUES (%s, %s)", (current_uid, uid))
            status = "followed"
    conn.commit()
    conn.close()

    return jsonify({"status": status, "uid": uid})


# ─────────────────────────────────────────────────────────────────────────────
# Closet Toggle
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/toggle_closet", methods=["POST"])
@login_required
def toggle_closet():
    data        = request.get_json()
    item_id     = data.get("item_id")
    current_uid = session["user_id"]

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM closet WHERE user_id = %s AND item_id = %s", (current_uid, item_id))
        existing = cur.fetchone()
        if existing:
            cur.execute("DELETE FROM closet WHERE user_id = %s AND item_id = %s", (current_uid, item_id))
            status = "removed"
        else:
            cur.execute("INSERT INTO closet (user_id, item_id) VALUES (%s, %s)", (current_uid, item_id))
            status = "added"
    conn.commit()
    conn.close()

    return jsonify({"status": status, "item_id": item_id})


# ─────────────────────────────────────────────────────────────────────────────
# Upload a post
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/upload_post", methods=["POST"])
@login_required
def upload_post():
    caption    = request.form.get("caption", "").strip()
    links      = request.form.get("links",   "").strip()
    photo      = request.files.get("photo")
    photo_path = None

if photo and photo.filename and allowed_file(photo.filename):
    result = cloudinary.uploader.upload(photo)
    photo_path = result["secure_url"]

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO posts (user_id, caption, photo_path, links) VALUES (%s, %s, %s, %s)",
            (session["user_id"], caption or None, photo_path, links or None)
        )
    conn.commit()
    conn.close()

    flash("Your look has been posted!")
    return redirect(url_for("profile_self"))


# ─────────────────────────────────────────────────────────────────────────────
# Upvote
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/upvote/<int:post_id>", methods=["POST"])
@login_required
def upvote(post_id):
    current_uid = session["user_id"]
    referrer    = request.referrer or url_for("home")

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM upvotes WHERE user_id = %s AND post_id = %s", (current_uid, post_id))
        already = cur.fetchone()
        if already:
            cur.execute("DELETE FROM upvotes WHERE user_id = %s AND post_id = %s", (current_uid, post_id))
            cur.execute("UPDATE posts SET votes = GREATEST(0, votes - 1) WHERE id = %s", (post_id,))
        else:
            cur.execute("INSERT INTO upvotes (user_id, post_id) VALUES (%s, %s)", (current_uid, post_id))
            cur.execute("UPDATE posts SET votes = votes + 1 WHERE id = %s", (post_id,))
    conn.commit()
    conn.close()

    return redirect(referrer)


# ─────────────────────────────────────────────────────────────────────────────
# Like
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/like/<int:post_id>", methods=["POST"])
@login_required
def like_post(post_id):
    current_uid = session["user_id"]

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM likes WHERE user_id = %s AND post_id = %s", (current_uid, post_id))
        existing = cur.fetchone()
        if existing:
            cur.execute("DELETE FROM likes WHERE user_id = %s AND post_id = %s", (current_uid, post_id))
            status = "unliked"
        else:
            cur.execute("INSERT INTO likes (user_id, post_id) VALUES (%s, %s)", (current_uid, post_id))
            status = "liked"
    conn.commit()
    conn.close()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or \
       request.accept_mimetypes.best == "application/json":
        return jsonify({"status": status, "post_id": post_id})
    referrer = request.referrer or url_for("profile_self")
    return redirect(referrer)


# ─────────────────────────────────────────────────────────────────────────────
# Track link click
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/track_link/<int:post_id>")
@login_required
def track_link(post_id):
    url = request.args.get("url", "").strip()
    if not url:
        return redirect("/")

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO link_clicks (post_id, url) VALUES (%s, %s)", (post_id, url))
    conn.commit()
    conn.close()

    if not url.startswith("http"):
        url = "https://" + url
    return redirect(url)


# ─────────────────────────────────────────────────────────────────────────────
# Delete post
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/delete_post/<int:post_id>", methods=["POST"])
@login_required
def delete_post(post_id):
    current_uid = session["user_id"]

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM posts WHERE id = %s AND user_id = %s", (post_id, current_uid))
        post = cur.fetchone()

        if post:
            if post["photo_path"]:
                full_path = os.path.join(BASE_DIR, "static", post["photo_path"])
                if os.path.exists(full_path):
                    os.remove(full_path)
            cur.execute("DELETE FROM items       WHERE post_id = %s", (post_id,))
            cur.execute("DELETE FROM upvotes     WHERE post_id = %s", (post_id,))
            cur.execute("DELETE FROM likes       WHERE post_id = %s", (post_id,))
            cur.execute("DELETE FROM link_clicks WHERE post_id = %s", (post_id,))
            cur.execute("DELETE FROM posts       WHERE id = %s",      (post_id,))
        else:
            flash("Post not found or you don't have permission.")
    conn.commit()
    conn.close()

    return redirect(url_for("profile_self"))


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
    # Run on startup
with app.app_context():
    init_db()
if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
   