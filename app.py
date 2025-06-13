from flask import Flask, render_template, request, redirect, url_for, session, flash, make_response, current_app
import sqlite3
import os
import re
import json
import pytz
from datetime import datetime
from werkzeug.utils import secure_filename
from typing import Optional
from sqlite3 import Connection
from functools import wraps
from datetime import timedelta
from typing import Optional, List, Dict, Tuple


app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
SECRET_KEY = 'a4c78f3ea9cc4f74bfb15efad9b012ee2342abcdefff1234'
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(minutes=10)


def init_db() -> None:
    """
    初始化資料庫，若不存在則建立 announcements 與 images 資料表。
    """
    if not os.path.exists('database.db'):
        try:
            conn = sqlite3.connect('database.db')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS announcements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    image TEXT,
                    timestamp TEXT NOT NULL
                );
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    announcement_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    is_cover INTEGER DEFAULT 0,
                    FOREIGN KEY (announcement_id) REFERENCES announcements (id)
                );
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS forms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    custom_fields JSON NOT NULL,
                    table_name TEXT,
                    created_at TEXT NOT NULL
                );
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS members (
                    iid INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    account TEXT NOT NULL UNIQUE,
                    password TEXT NOT NULL
                );
            ''')
            conn.execute('''
                INSERT OR IGNORE INTO members (username, account, password)
                VALUES (?, ?, ?)
            ''', ('admin', 'admin', 'admin'))

            conn.commit()
        except sqlite3.Error as e:
            print(f"資料庫錯誤: {e}")
        finally:
            conn.close()


def get_db_connection() -> Connection:
    """
    建立並回傳資料庫連線。
    """
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('請先登入')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


def _validate_and_prepare_fields(fields_input: str) -> Tuple[Optional[str], Optional[List[Dict[str, str]]]]:
    """驗證自訂欄位輸入並準備儲存結構。回傳 (錯誤訊息, 欄位資料)。"""
    if not fields_input:
        return None, []

    fields_raw = list(dict.fromkeys([f.strip() for f in re.split(r'[,\s]+|，', fields_input) if f.strip()]))

    if len(fields_raw) > 10:
        return '自訂欄位數量不得超過10個！', None

    for field in fields_raw:
        if len(field) > 50:
            return f'自訂欄位 "{field}" 過長，長度不得超過50個字符！', None

    custom_fields_data = [
        {
            "original_name": name,
            "sanitized_name": name  
        }
        for i, name in enumerate(fields_raw)
    ]
    return None, custom_fields_data


def get_all_forms() -> List[sqlite3.Row]:
    """取得所有表單的列表。"""
    with get_db_connection() as conn:
        return conn.execute('SELECT * FROM forms ORDER BY id DESC').fetchall()


def get_form_by_id(form_id: int) -> Optional[sqlite3.Row]:
    """根據 ID 取得單一表單。"""
    with get_db_connection() as conn:
        return conn.execute('SELECT * FROM forms WHERE id = ?', (form_id,)).fetchone()


def create_form_and_table(title: str, description: str, custom_fields_data: List[Dict[str, str]]) -> None:
    """
    以交易方式創建新表單及其對應的提交資料表。
    確保所有操作要麼全部成功，要麼全部失敗。
    """
    with get_db_connection() as conn:
        custom_fields_json = json.dumps(custom_fields_data)
        tz = pytz.timezone('Asia/Taipei')
        created_at: str = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')

        # 步驟 1: 插入表單元數據
        cursor = conn.execute(
            'INSERT INTO forms (title, description, custom_fields, created_at) VALUES (?, ?, ?, ?)',
            (title, description, custom_fields_json, created_at)
        )
        form_id = cursor.lastrowid
        table_name = f"form_submissions_{form_id}"

        # 步驟 2: 更新 table_name
        conn.execute('UPDATE forms SET table_name = ? WHERE id = ?', (table_name, form_id))

        # 步驟 3: 動態建立提交記錄資料表
        columns = [
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "name TEXT NOT NULL", "email TEXT NOT NULL", "phone TEXT NOT NULL",
            "timestamp TEXT NOT NULL"
        ]

        columns.extend(f'"{field["original_name"]}" TEXT' for field in custom_fields_data)
        conn.execute(f"CREATE TABLE {table_name} ({', '.join(columns)});")

        conn.commit()


def delete_form_and_table(form_id: int) -> bool:
    """刪除表單及其對應的提交資料表。"""
    form = get_form_by_id(form_id)
    if not form or not form['table_name']:
        return False

    with get_db_connection() as conn:
        conn.execute(f"DROP TABLE IF EXISTS {form['table_name']}")
        conn.execute('DELETE FROM forms WHERE id = ?', (form_id,))
        conn.commit()
    return True


def save_submission(form: sqlite3.Row, form_data: Dict[str, any]) -> None:
    """儲存一筆表單提交記錄。"""
    custom_fields = json.loads(form['custom_fields'])
    tz = pytz.timezone('Asia/Taipei')

    columns = ['name', 'email', 'phone', 'timestamp']
    values = [
        form_data['name'], form_data['email'], form_data['phone'],
        datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    ]

    for field in custom_fields:
        columns.append(f'"{field["original_name"]}"')
        values.append(form_data.get(field['original_name'], ''))

    placeholders = ', '.join(['?'] * len(values))
    sql = f"INSERT INTO {form['table_name']} ({', '.join(columns)}) VALUES ({placeholders})"

    with get_db_connection() as conn:
        conn.execute(sql, tuple(values))
        conn.commit()


def get_submissions_with_ordered_headers(form_id: int) -> Tuple[List[str], List[List[any]]]:
    """取得特定表單的提交記錄，並回傳排序好的表頭和資料。"""
    form = get_form_by_id(form_id)
    if not form or not form['table_name']:
        return [], []

    with get_db_connection() as conn:
        # 1. 取得資料庫中實際存在的欄位
        cursor = conn.execute(f"PRAGMA table_info({form['table_name']})")
        db_columns = {row['name'] for row in cursor}

        # 2. 定義欄位順序和顯示名稱
        header_map = {'name': '姓名', 'email': '電子郵件', 'phone': '手機號碼', 'timestamp': '提交時間'}
        custom_fields_map = {f['original_name']: f['original_name'] for f in json.loads(form['custom_fields'])}

        # 依序建立最終要查詢的欄位和顯示的表頭
        query_cols, display_headers = [], []

        # 標準欄位
        for col in ['name', 'email', 'phone']:
            if col in db_columns:
                query_cols.append(f'"{col}"')
                display_headers.append(header_map[col])

        # 自訂欄位 (依照創建順序)
        for san_name, org_name in custom_fields_map.items():
                if san_name in db_columns:
                    query_cols.append(f'"{san_name}"')
                    display_headers.append(org_name)

        # 時間戳欄位
        if 'timestamp' in db_columns:
            query_cols.append('"timestamp"')
            display_headers.append(header_map['timestamp'])

        # 3. 查詢資料
        if not query_cols:
            return [], []

        sql = f'SELECT {", ".join(query_cols)} FROM {form["table_name"]} ORDER BY id DESC'
        rows = conn.execute(sql).fetchall()
        submissions_data = [list(row) for row in rows]

        return display_headers, submissions_data


@app.route('/')
def index():
    """
    首頁，顯示最新三則公告。
    """
    try:
        conn = get_db_connection()
        announcements = conn.execute(
            'SELECT * FROM announcements ORDER BY id DESC LIMIT 3'
        ).fetchall()
    finally:
        conn.close()
    return render_template('index.html', announcements=announcements)


@app.route('/member')
def member():
    """
    奉祀神祇頁
    """
    return render_template('member.html')


@app.route('/history')
def history():
    """
    神明故事頁
    """
    return render_template('history.html')


@app.route('/event')
def event():
    """
    廟宇沿革頁
    """
    return render_template('event.html')


@app.route('/light')
def light():
    """
    安奉斗燈頁
    """
    return render_template('light.html')


@app.route('/solve')
def solve():
    """
    收驚問事頁
    """
    return render_template('solve.html')


@app.route('/announcements')
def announcement_list():
    """
    公告列表頁面。
    """
    try:
        conn = get_db_connection()
        announcements = conn.execute(
            'SELECT * FROM announcements ORDER BY id DESC'
        ).fetchall()
    finally:
        conn.close()
    return render_template('announcements.html', announcements=announcements)


@app.route('/announcements/<int:announcement_id>')
def announcement_detail(announcement_id: int):
    """
    公告詳情頁。
    """
    try:
        conn = get_db_connection()
        announcement = conn.execute(
            'SELECT * FROM announcements WHERE id = ?',
            (announcement_id,)
        ).fetchone()
        images = conn.execute(
            'SELECT * FROM images WHERE announcement_id = ?',
            (announcement_id,)
        ).fetchall()
    finally:
        conn.close()
    return render_template('announcement_detail.html', announcement=announcement, images=images)


@app.route('/admin_announcements')
@login_required
def admin_announcements():
    """
    管理頁面，顯示所有公告。
    """
    try:
        conn = get_db_connection()
        announcements = conn.execute(
            'SELECT * FROM announcements ORDER BY id DESC'
        ).fetchall()
    finally:
        conn.close()
    return render_template('admin_announcements.html', announcements=announcements)


@app.route('/admin_announcements/create_announcements', methods=['GET', 'POST'])
@login_required
def create_announcements():
    """
    新增公告。
    """
    if request.method == 'POST':
        title: str = request.form['title']
        content: str = request.form['content']
        image = request.files.get('image')
        images = request.files.getlist('images')
        tz = pytz.timezone('Asia/Taipei')
        timestamp: str = datetime.now(tz).strftime('%Y-%m-%d')

        image_filename: Optional[str] = None
        if image and image.filename:
            filename = secure_filename(image.filename)
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            image.save(image_path)
            image_filename = filename

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO announcements (title, content, image, timestamp) VALUES (?, ?, ?, ?)',
                (title, content, image_filename, timestamp)
            )
            announcement_id = cursor.lastrowid

            for img in images:
                if img and img.filename:
                    img_name = secure_filename(img.filename)
                    img_path = os.path.join(app.config['UPLOAD_FOLDER'], img_name)
                    img.save(img_path)
                    cursor.execute(
                        'INSERT INTO images (announcement_id, filename) VALUES (?, ?)',
                        (announcement_id, img_name)
                    )
            conn.commit()
        except sqlite3.Error as e:
            print(f"資料庫錯誤: {e}")
        finally:
            conn.close()

        return redirect(url_for('admin_announcements'))

    return render_template('create_announcements.html')


@app.route('/admin_announcements/delete/<int:announcement_id>')
@login_required
def delete(announcement_id: int):
    """
    刪除公告。
    """
    try:
        conn = get_db_connection()
        conn.execute(
            'DELETE FROM announcements WHERE id = ?',
            (announcement_id,)
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"刪除失敗: {e}")
    finally:
        conn.close()
    return redirect(url_for('admin_announcements'))


@app.route('/admin_announcements/delete-image/<int:image_id>/<int:announcement_id>')
@login_required
def delete_image(image_id: int, announcement_id: int):
    """
    刪除公告的一張附加圖片。
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        image = cursor.execute('SELECT filename FROM images WHERE id = ?', (image_id,)).fetchone()
        if image:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], image['filename'])
            if os.path.exists(file_path):
                os.remove(file_path)
            cursor.execute('DELETE FROM images WHERE id = ?', (image_id,))
            conn.commit()
    except sqlite3.Error as e:
        print(f"圖片刪除失敗: {e}")
    finally:
        conn.close()
    return redirect(url_for('edit', announcement_id=announcement_id))


@app.route('/admin_announcements/edit/<int:announcement_id>', methods=['GET', 'POST'])
@login_required
def edit(announcement_id: int):
    """
    編輯公告。
    """
    conn = get_db_connection()
    announcement = conn.execute(
        'SELECT * FROM announcements WHERE id = ?',
        (announcement_id,)
    ).fetchone()

    # 讀取目前所有附加圖片
    other_images = conn.execute(
        'SELECT * FROM images WHERE announcement_id = ?',
        (announcement_id,)
    ).fetchall()

    if request.method == 'POST':
        title: str = request.form['title']
        content: str = request.form['content']
        image = request.files.get('image')
        new_images = request.files.getlist('images')

        image_filename: str = announcement['image']
        if image and image.filename:
            filename = secure_filename(image.filename)
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_filename = filename

        try:
            conn.execute(
                'UPDATE announcements SET title = ?, content = ?, image = ? WHERE id = ?',
                (title, content, image_filename, announcement_id)
            )

            # 新增新上傳的多張圖片
            for img in new_images:
                if img and img.filename:
                    img_name = secure_filename(img.filename)
                    img_path = os.path.join(app.config['UPLOAD_FOLDER'], img_name)
                    img.save(img_path)
                    conn.execute(
                        'INSERT INTO images (announcement_id, filename) VALUES (?, ?)',
                        (announcement_id, img_name)
                    )

            conn.commit()
        except sqlite3.Error as e:
            print(f"更新失敗: {e}")
        finally:
            conn.close()
        return redirect(url_for('admin_announcements'))

    conn.close()
    return render_template('edit_announcement.html', announcement=announcement, other_images=other_images)


@app.route('/admin_form')
@login_required
def admin_form():
    """表單管理主頁。"""
    try:
        forms = get_all_forms()
    except sqlite3.Error:
        forms = []
    return render_template('admin_form.html', forms=forms)


@app.route('/admin_form/create_form', methods=['GET', 'POST'])
@login_required
def create_form():
    """創建新表單。"""
    if request.method == 'POST':
        title = request.form['title'].strip()
        description = request.form['description'].strip()
        custom_fields_input = request.form.get('custom_fields', '').strip()

        if not title:
            return render_template('create_form.html', **request.form)

        error_msg, fields_data = _validate_and_prepare_fields(custom_fields_input)
        if error_msg:
            return render_template('create_form.html', **request.form)

        try:
            create_form_and_table(title, description, fields_data)
        except sqlite3.Error:
            pass

        return redirect(url_for('admin_form'))

    return render_template('create_form.html')


@app.route('/admin_form/delete_form/<int:form_id>')
@login_required
def delete_form(form_id: int):
    """刪除表單及其提交記錄。"""
    try:
        if delete_form_and_table(form_id):
            pass
        else:
            pass
    except sqlite3.Error:
        pass
    return redirect(url_for('admin_form'))


@app.route('/form/<int:form_id>', methods=['GET', 'POST'])
def form_dynamic(form_id: int):
    """顯示和處理動態表單。"""
    try:
        form = get_form_by_id(form_id)
        if not form:
            return render_template('404.html'), 404

        if request.method == 'POST':
            if not form['table_name']:
                return redirect(url_for('form_dynamic', form_id=form_id))

            save_submission(form, request.form)
            return redirect(url_for('form_submitted'))

        custom_fields = json.loads(form['custom_fields'])
        original_field_names = [field['original_name'] for field in custom_fields]
        return render_template('form_dynamic.html', form=form, custom_fields=original_field_names)

    except sqlite3.Error:
        return render_template('500.html'), 500


@app.route('/form_submitted')
def form_submitted():
    """表單提交成功頁面。"""
    return render_template('form_submitted.html')


@app.route('/admin_form/form_submissions')
@login_required
def admin_form_submissions():
    """後台顯示所有表單的提交記錄。"""
    selected_form_id = request.args.get('form_id', type=int)
    headers, submissions_data, forms = [], [], []

    try:
        forms = get_all_forms()
        if selected_form_id:
            headers, submissions_data = get_submissions_with_ordered_headers(selected_form_id)
    except sqlite3.Error:
        pass

    return render_template(
        'admin_form_all.html',
        submissions=submissions_data,
        forms=forms,
        selected_form_id=selected_form_id,
        headers=headers
    )


@app.route('/admin')
def admin():
    """
    後臺首頁 未登入
    """
    return render_template('admin.html')


@app.route('/login', methods=['GET', 'POST'])
def admin_login():
    """
    後臺登入
    """
    if request.method == 'POST':
        account = request.form.get('account')
        password = request.form.get('password')

        if not account or not password:
            return render_template('admin_error.html', message='請輸入帳號和密碼')

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT iid, username FROM members WHERE account = ? AND password = ?', (account, password))
            row = cursor.fetchone()
            if not row:
                return render_template('admin_error.html', message='帳號或密碼錯誤')

            iid, username = row['iid'], row['username']
            session['logged_in'] = True
            session['username'] = username
            session['iid'] = iid
            session.permanent = True

            return redirect(url_for('admin_welcome'))

    return render_template('admin_login.html')


@app.route('/admin_welcome')
@login_required
def admin_welcome():
    """
    後臺首頁 已登入
    """
    username = session.get('username')
    iid = session.get('iid')
    return render_template('admin_welcome.html', username=username, iid=iid)


@app.route('/logout')
def logout():
    """
    後臺登出
    """
    session.clear()
    response = make_response(redirect(url_for('admin_login')))
    response.delete_cookie(current_app.config['SESSION_COOKIE_NAME'])
    return response

@app.route('/edit_profile/<int:iid>', methods=['GET', 'POST'])
@login_required
def admin_edit_profile(iid: int):
    """
    後臺管理員檔案編輯
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if request.method == 'POST':
            account = request.form.get('account')
            password = request.form.get('password')

            if not account or not password:
                return render_template('admin_error.html', message='請輸入帳號和密碼')

            cursor.execute('SELECT * FROM members WHERE account = ? AND iid != ?', (account, iid))
            if cursor.fetchone():
                return render_template('admin_error.html', message='帳號已被使用')

            cursor.execute('''
                UPDATE members SET account = ?, password = ?
                WHERE iid = ?
            ''', (account, password, iid))
            conn.commit()
            cursor.execute('SELECT username FROM members WHERE iid = ?', (iid,))
            username = cursor.fetchone()['username']
            return render_template('admin_welcome.html', username=username, iid=iid)

        cursor.execute('SELECT * FROM members WHERE iid = ?', (iid,))
        user = cursor.fetchone()
        if user:
            return render_template('admin_edit_profile.html', user=user)
        return render_template('admin_error.html', message='找不到用戶')


@app.route('/delete/<int:iid>')
@login_required
def admin_delete_user(iid: int):
    """
    後臺管理員檔案刪除
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM members WHERE iid = ?', (iid,))
        conn.commit()
    return redirect(url_for('admin'))


init_db()


@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001)
