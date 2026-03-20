from flask import Flask, render_template, request, redirect, session, flash 
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

MAX_CAPACITY = 14


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=5432,
        connect_timeout=5
    )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/reserve", methods=["POST"])
def reserve():
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:

                date = request.form.get("date")
                party_size = int(request.form.get("party_size", 1))

                names = request.form.getlist("attendee_name")
                contacts = request.form.getlist("attendee_contact")

                attendees = [(n.strip(), c.strip()) for n, c in zip(names, contacts) if n.strip()]

                if len(attendees) != party_size:
                    return "참석자 수가 일치하지 않습니다.", 400

                cur.execute("""
                    SELECT a.id
                    FROM attendees a
                    JOIN reservations r ON a.reservation_id = r.id
                    WHERE r.date=%s
                    AND r.canceled_at IS NULL
                    FOR UPDATE
                """, (date,))

                cur.execute("""
                    SELECT COUNT(*)
                    FROM attendees a
                    JOIN reservations r ON a.reservation_id = r.id
                    WHERE r.date=%s
                    AND a.status='confirmed'
                    AND r.canceled_at IS NULL
                """, (date,))
                confirmed = cur.fetchone()[0]

                cur.execute("""
                    INSERT INTO reservations (reserver_name, reserver_contact, date, party_size)
                    VALUES (%s,%s,%s,%s)
                    RETURNING id
                """, (
                    request.form["reserver_name"],
                    request.form["reserver_contact"],
                    date,
                    party_size
                ))
                reservation_id = cur.fetchone()[0]

                for name, contact in attendees:
                    status = "confirmed" if confirmed < MAX_CAPACITY else "waiting"
                    if status == "confirmed":
                        confirmed += 1

                    cur.execute("""
                        INSERT INTO attendees (reservation_id, attendee_name, attendee_contact, status)
                        VALUES (%s,%s,%s,%s)
                    """, (reservation_id, name, contact, status))

        return redirect(f"/result/{reservation_id}")

    finally:
        conn.close()


@app.route("/status")
def status():
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT r.date,
                       COUNT(*) FILTER (WHERE a.status='confirmed'),
                       COUNT(*) FILTER (WHERE a.status='waiting')
                FROM reservations r
                JOIN attendees a ON r.id = a.reservation_id
                WHERE r.canceled_at IS NULL
                GROUP BY r.date
                ORDER BY r.date
            """)
            rows = cur.fetchall()

            cur.execute("SELECT date, course_name, start_time FROM courses")
            course_rows = cur.fetchall()

    conn.close()

    courses = {c[0]: (c[1], c[2]) for c in course_rows}

    return render_template(
        "status.html",
        rows=rows,
        courses=courses,
        max_capacity=MAX_CAPACITY
    )


@app.route("/result/<int:rid>")
def result(rid):
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT a.status,
                       CASE
                         WHEN a.status='waiting'
                         THEN ROW_NUMBER() OVER (ORDER BY a.id)
                       END
                FROM attendees a
                WHERE reservation_id=%s
            """, (rid,))
            rows = cur.fetchall()

    conn.close()
    return render_template("result.html", rows=rows)


@app.route("/status/<date>")
def status_detail(date):
    conn = get_conn()

    with conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT id, reserver_name, reserver_contact, canceled_at
                FROM reservations
                WHERE date=%s
                ORDER BY id
            """, (date,))
            reservations = cur.fetchall()

            cur.execute("""
                SELECT reservation_id,attendee_name,attendee_contact, status,
                  CASE
                  WHEN status='waiting'
                  THEN ROW_NUMBER() OVER (PARTITION BY status ORDER BY id)
                   END AS wait_order
                  FROM attendees
                 WHERE reservation_id IN (
                                            SELECT id FROM reservations WHERE date=%s
                                          )
            """, (date,))
            rows = cur.fetchall()

    conn.close()

    attendees_map = {}
    for rid, name, contact, status, wait_order in rows:
        attendees_map.setdefault(rid, []).append((name, contact, status, wait_order))

    return render_template(
        "status_detail.html",
        date=date,
        reservations=reservations,
        attendees_map=attendees_map
    )


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        conn = get_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM admin WHERE username=%s AND password_hash=%s",
                    (request.form["username"], request.form["password"])
                )
                user = cur.fetchone()

        if user:
            session["admin"] = True
            return redirect("/manage")
        else:
            flash("로그인 실패")

    return render_template("admin_login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/manage")
def manage():
    if "admin" not in session:
        return redirect("/admin")

    conn = get_conn()
    with conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT r.date,
                       COUNT(*) FILTER (WHERE a.status='confirmed'),
                       COUNT(*) FILTER (WHERE a.status='waiting')
                FROM reservations r
                JOIN attendees a ON r.id = a.reservation_id
                WHERE r.canceled_at IS NULL
                GROUP BY r.date
                ORDER BY r.date
            """)
            rows = cur.fetchall()

            cur.execute("SELECT date, course_name, start_time FROM courses")
            course_rows = cur.fetchall()

    conn.close()

    courses = {c[0]: (c[1], c[2]) for c in course_rows}

    return render_template(
        "manage.html",
        rows=rows,
        courses=courses,
        max_capacity=MAX_CAPACITY
    )


@app.route("/manage/<date>")
def manage_detail(date):
    if "admin" not in session:
        return redirect("/admin")

    conn = get_conn()

    with conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT id, reserver_name, reserver_contact, canceled_at
                FROM reservations
                WHERE date=%s
                ORDER BY id
            """, (date,))
            reservations = cur.fetchall()

            cur.execute("""
                SELECT reservation_id,
                       attendee_name,
                       attendee_contact,
                       status,
                       CASE
                         WHEN status='waiting'
                         THEN ROW_NUMBER() OVER (PARTITION BY status ORDER BY id)
                       END AS wait_order
                FROM attendees
                WHERE reservation_id IN (
                    SELECT id FROM reservations WHERE date=%s
                )
            """, (date,))
            rows = cur.fetchall()

    conn.close()

    attendees_map = {}
    for rid, name, contact, status, wait_order in rows:
        attendees_map.setdefault(rid, []).append((name, contact, status, wait_order))

    return render_template(
        "manage_detail.html",
        date=date,
        reservations=reservations,
        attendees_map=attendees_map
    )


@app.route("/cancel/<int:id>")
def cancel(id):
    if "admin" not in session:
        return redirect("/admin")

    conn = get_conn()
    with conn:
        with conn.cursor() as cur:

            cur.execute("UPDATE reservations SET canceled_at=NOW() WHERE id=%s", (id,))
            cur.execute("UPDATE attendees SET status='canceled' WHERE reservation_id=%s", (id,))

            cur.execute("SELECT date FROM reservations WHERE id=%s", (id,))
            date = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM attendees a
                JOIN reservations r ON a.reservation_id = r.id
                WHERE r.date=%s
                AND a.status='confirmed'
                AND r.canceled_at IS NULL
            """, (date,))
            confirmed = cur.fetchone()[0]

            while confirmed < MAX_CAPACITY:
                cur.execute("""
                    SELECT a.id
                    FROM attendees a
                    JOIN reservations r ON a.reservation_id = r.id
                    WHERE r.date=%s
                    AND a.status='waiting'
                    AND r.canceled_at IS NULL
                    ORDER BY a.id
                    LIMIT 1
                """, (date,))
                row = cur.fetchone()

                if not row:
                    break

                cur.execute("UPDATE attendees SET status='confirmed' WHERE id=%s", (row[0],))
                confirmed += 1

    conn.close()
    return redirect("/manage")


@app.route("/manage/add", methods=["GET", "POST"])
def add_reservation():
    if "admin" not in session:
        return redirect("/admin")

    conn = get_conn()

    if request.method == "POST":
        with conn:
            with conn.cursor() as cur:

                date = request.form["date"]
                name = request.form["reserver_name"]
                contact = request.form["reserver_contact"]

                cur.execute("""
                    INSERT INTO reservations (reserver_name, reserver_contact, date, party_size)
                    VALUES (%s,%s,%s,1)
                    RETURNING id
                """, (name, contact, date))

                rid = cur.fetchone()[0]

                cur.execute("""
                    INSERT INTO attendees (reservation_id, attendee_name, attendee_contact, status)
                    VALUES (%s,%s,%s,'confirmed')
                """, (rid, name, contact))

        conn.close()
        return redirect("/manage")

    return render_template("add_reservation.html")


@app.route("/courses", methods=["GET", "POST"])
def courses():
    if "admin" not in session:
        return redirect("/admin")

    conn = get_conn()

    if request.method == "POST":
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO courses (date, course_name, start_time)
                    VALUES (%s,%s,%s)
                    ON CONFLICT (date)
                    DO UPDATE SET
                        course_name=EXCLUDED.course_name,
                        start_time=EXCLUDED.start_time
                """, (
                    request.form["date"],
                    request.form["course_name"],
                    request.form["start_time"]
                ))

    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM courses ORDER BY date")
            rows = cur.fetchall()

    conn.close()
    return render_template("courses.html", rows=rows)


@app.route("/my")
def my():
    phone = request.args.get("phone")

    conn = get_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.date, a.attendee_name, a.status
                FROM reservations r
                JOIN attendees a ON r.id = a.reservation_id
                WHERE r.reserver_contact=%s
                ORDER BY r.date
            """, (phone,))
            rows = cur.fetchall()

    conn.close()
    return render_template("my.html", rows=rows)


# 🔥🔥🔥 핵심 (Render용)
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)