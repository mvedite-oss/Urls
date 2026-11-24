import os, sqlite3, secrets, random
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_from_directory, abort)
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR   = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
IMG_DIR    = os.path.join(UPLOAD_DIR, 'images')
FILE_DIR   = os.path.join(UPLOAD_DIR, 'files')
DB_PATH    = os.path.join(BASE_DIR, 'mvmodes.db')

for d in (UPLOAD_DIR, IMG_DIR, FILE_DIR):
    os.makedirs(d, exist_ok=True)

app = Flask(__name__, template_folder=BASE_DIR, static_folder=BASE_DIR, static_url_path='')
app.config['SECRET_KEY'] = os.environ.get('MV_SECRET_KEY', 'mv-modes-' + secrets.token_hex(16))
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

ALLOWED_IMG  = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_FILE = {'apk', 'zip', 'rar', '7z', 'obb'}
SHAPES = ['triangulo', 'quadrado', 'circulo', 'trapezio',
          'losango', 'pentagono', 'hexagono']

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

def init_db():
    db = get_db()
    db.executescript('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        slug TEXT UNIQUE
    );
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        short_description TEXT,
        category_id INTEGER,
        type TEXT DEFAULT 'game',
        version TEXT,
        cover_image TEXT,
        file_path TEXT,
        file_original_name TEXT,
        file_size INTEGER,
        download_count INTEGER DEFAULT 0,
        mission_enabled INTEGER DEFAULT 1,
        mission_rounds INTEGER DEFAULT 3,
        mission_options INTEGER DEFAULT 4,
        is_featured INTEGER DEFAULT 0,
        featured_order INTEGER DEFAULT 0,
        published_at TEXT,
        FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS download_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        item_id INTEGER NOT NULL,
        mission_passed INTEGER DEFAULT 0,
        used_count INTEGER DEFAULT 0,
        max_uses INTEGER DEFAULT 0,
        expires_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
    );
    ''')
    if db.execute('SELECT COUNT(*) c FROM users').fetchone()['c'] == 0:
        db.execute('INSERT INTO users (username, password_hash, is_admin) VALUES (?,?,1)',
                   ('admin', generate_password_hash('admin123')))
    if db.execute('SELECT COUNT(*) c FROM categories').fetchone()['c'] == 0:
        for name in ['Ação', 'Aventura', 'RPG', 'Corrida', 'Estratégia',
                     'Mods APK', 'Ferramentas', 'Arcade']:
            slug = ''.join(c for c in name.lower()
                           if c.isalnum() or c == '-').replace(' ', '-')
            db.execute('INSERT INTO categories (name, slug) VALUES (?,?)', (name, slug))
    db.commit()
    db.close()

init_db()

def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get('admin_id'):
            return redirect(url_for('admin_login'))
        return f(*a, **kw)
    return wrapper

def make_slug(name):
    return ''.join(c for c in name.lower()
                   if c.isalnum() or c == '-').replace(' ', '-') or secrets.token_hex(4)

# ---------- PÚBLICO ----------
@app.route('/')
def index():
    db = get_db()
    featured = db.execute('''SELECT i.*, c.name AS cat FROM items i
        LEFT JOIN categories c ON c.id = i.category_id
        WHERE i.is_featured = 1
        ORDER BY i.featured_order ASC, i.published_at DESC LIMIT 1''').fetchone()
    popular = db.execute('''SELECT i.*, c.name AS cat FROM items i
        LEFT JOIN categories c ON c.id = i.category_id
        ORDER BY i.download_count DESC, i.published_at DESC LIMIT 8''').fetchall()
    mods = db.execute('''SELECT i.*, c.name AS cat FROM items i
        LEFT JOIN categories c ON c.id = i.category_id
        WHERE i.type = 'mod' ORDER BY i.published_at DESC LIMIT 8''').fetchall()
    files = db.execute('''SELECT i.*, c.name AS cat FROM items i
        LEFT JOIN categories c ON c.id = i.category_id
        WHERE i.type = 'file' ORDER BY i.published_at DESC LIMIT 8''').fetchall()
    db.close()
    return render_template('site.html', page='index', featured=featured,
                           popular=popular, mods=mods, files=files)

@app.route('/search')
def search():
    q = request.args.get('q', '').strip()
    t = request.args.get('type')
    db = get_db()
    sql = """SELECT i.*, c.name AS cat FROM items i
             LEFT JOIN categories c ON c.id = i.category_id WHERE 1=1"""
    params = []
    if q:
        sql += " AND (i.title LIKE ? OR i.description LIKE ?)"
        params += [f'%{q}%', f'%{q}%']
    if t:
        sql += " AND i.type = ?"
        params.append(t)
    sql += " ORDER BY i.published_at DESC"
    items = db.execute(sql, params).fetchall()
    db.close()
    return render_template('site.html', page='search', items=items, q=q)

@app.route('/item/<int:item_id>')
def item_detail(item_id):
    db = get_db()
    item = db.execute('''SELECT i.*, c.name AS cat FROM items i
        LEFT JOIN categories c ON c.id = i.category_id WHERE i.id = ?''',
        (item_id,)).fetchone()
    db.close()
    if not item: abort(404)
    return render_template('site.html', page='item', item=item)

@app.route('/download/<int:item_id>')
def download_page(item_id):
    db = get_db()
    item = db.execute('''SELECT i.*, c.name AS cat FROM items i
        LEFT JOIN categories c ON c.id = i.category_id WHERE i.id = ?''',
        (item_id,)).fetchone()
    db.close()
    if not item: abort(404)
    return render_template('site.html', page='download', item=item)

@app.route('/download/<int:item_id>/direct')
def download_direct(item_id):
    db = get_db()
    item = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
    if not item or item['mission_enabled'] or not item['file_path']:
        db.close(); abort(403)
    db.execute('UPDATE items SET download_count = download_count + 1 WHERE id=?', (item_id,))
    db.commit(); db.close()
    return send_from_directory(FILE_DIR, item['file_path'],
                               as_attachment=True,
                               download_name=item['file_original_name'] or item['file_path'])

@app.route('/d/<token>')
def token_page(token):
    db = get_db()
    t = db.execute('''SELECT t.*, i.title, i.description, i.short_description,
        i.cover_image, i.file_size, i.version, i.published_at, c.name AS cat
        FROM download_tokens t JOIN items i ON i.id = t.item_id
        LEFT JOIN categories c ON c.id = i.category_id
        WHERE t.token = ?''', (token,)).fetchone()
    db.close()
    if not t: abort(404)
    if t['expires_at'] and datetime.fromisoformat(t['expires_at']) < datetime.utcnow():
        abort(410)
    return render_template('site.html', page='token', token=t)

@app.route('/d/<token>/file')
def token_file(token):
    db = get_db()
    t = db.execute('''SELECT t.*, i.file_path, i.file_original_name
        FROM download_tokens t JOIN items i ON i.id = t.item_id
        WHERE t.token = ?''', (token,)).fetchone()
    if not t or not t['file_path']:
        db.close(); abort(404)
    if t['expires_at'] and datetime.fromisoformat(t['expires_at']) < datetime.utcnow():
        db.close(); abort(410)
    if t['max_uses'] > 0 and t['used_count'] >= t['max_uses']:
        db.close(); abort(410)
    db.execute('UPDATE download_tokens SET used_count = used_count + 1 WHERE id=?', (t['id'],))
    db.execute('UPDATE items SET download_count = download_count + 1 WHERE id=?', (t['item_id'],))
    db.commit(); db.close()
    return send_from_directory(FILE_DIR, t['file_path'],
                               as_attachment=True,
                               download_name=t['file_original_name'] or t['file_path'])

@app.route('/uploads/images/<path:filename>')
def uploaded_image(filename):
    return send_from_directory(IMG_DIR, filename)

# ---------- MISSÃO ----------
def generate_challenge(num_rounds, num_options):
    rounds = []
    for _ in range(num_rounds):
        target = random.choice(SHAPES)
        options = [target]
        while len(options) < num_options:
            s = random.choice(SHAPES)
            if s not in options:
                options.append(s)
        random.shuffle(options)
        rounds.append({'target': target, 'options': options})
    return rounds

@app.route('/api/mission/start', methods=['POST'])
def api_mission_start():
    data = request.get_json(silent=True) or {}
    item_id = data.get('item_id')
    db = get_db()
    item = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
    db.close()
    if not item:
        return jsonify(success=False, error='Item não encontrado'), 404
    rounds = generate_challenge(item['mission_rounds'] or 3,
                                item['mission_options'] or 4)
    cid = secrets.token_urlsafe(16)
    challenges = session.get('challenges', {})
    challenges[cid] = {'item_id': item_id, 'rounds': rounds, 'current': 0}
    session['challenges'] = challenges
    r = rounds[0]
    return jsonify(success=True, challenge_id=cid, total=len(rounds),
                   round={'index': 0, 'target': r['target'], 'options': r['options']})

@app.route('/api/mission/answer', methods=['POST'])
def api_mission_answer():
    data = request.get_json(silent=True) or {}
    cid = data.get('challenge_id')
    shape = data.get('shape')
    challenges = session.get('challenges', {})
    ch = challenges.get(cid)
    if not ch:
        return jsonify(success=False, error='Desafio expirado'), 400
    current = ch['current']
    expected = ch['rounds'][current]['target']
    if shape != expected:
        return jsonify(success=True, correct=False, expected=expected)
    ch['current'] += 1
    challenges[cid] = ch
    session['challenges'] = challenges
    if ch['current'] >= len(ch['rounds']):
        token = secrets.token_urlsafe(24)
        expires = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        db = get_db()
        db.execute('''INSERT INTO download_tokens (token, item_id, mission_passed, expires_at)
                      VALUES (?,?,1,?)''', (token, ch['item_id'], expires))
        db.commit(); db.close()
        del challenges[cid]
        session['challenges'] = challenges
        return jsonify(success=True, correct=True, done=True,
                       download_url=url_for('token_file', token=token))
    nxt = ch['rounds'][ch['current']]
    return jsonify(success=True, correct=True, done=False,
                   round={'index': ch['current'], 'target': nxt['target'],
                          'options': nxt['options']})

# ---------- ADM ----------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username=? AND is_admin=1',
                          (username,)).fetchone()
        db.close()
        if user and check_password_hash(user['password_hash'], password):
            session['admin_id'] = user['id']
            session['admin_username'] = user['username']
            return redirect(url_for('admin_dashboard'))
        flash('Usuário ou senha inválidos.', 'error')
    return render_template('site.html', page='admin_login')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None)
    session.pop('admin_username', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db()
    items = db.execute('''SELECT i.*, c.name AS cat FROM items i
        LEFT JOIN categories c ON c.id = i.category_id
        ORDER BY i.published_at DESC''').fetchall()
    cats = db.execute('SELECT * FROM categories ORDER BY name').fetchall()
    tokens = db.execute('''SELECT t.*, i.title FROM download_tokens t
        JOIN items i ON i.id = t.item_id
        ORDER BY t.created_at DESC LIMIT 50''').fetchall()
    stats = {
        'games': db.execute("SELECT COUNT(*) c FROM items WHERE type='game'").fetchone()['c'],
        'mods':  db.execute("SELECT COUNT(*) c FROM items WHERE type='mod'").fetchone()['c'],
        'files': db.execute("SELECT COUNT(*) c FROM items WHERE type='file'").fetchone()['c'],
        'downloads': db.execute('SELECT COALESCE(SUM(download_count),0) s FROM items').fetchone()['s'],
    }
    edit_item = None
    edit_id = request.args.get('edit')
    if edit_id:
        edit_item = db.execute('SELECT * FROM items WHERE id=?', (edit_id,)).fetchone()
    db.close()
    return render_template('site.html', page='admin', items=items, cats=cats,
                           tokens=tokens, stats=stats, edit_item=edit_item)

@app.route('/admin/items/create', methods=['POST'])
@admin_required
def admin_item_create():
    return save_item(None)

@app.route('/admin/items/<int:item_id>/edit', methods=['POST'])
@admin_required
def admin_item_edit(item_id):
    return save_item(item_id)

def save_item(item_id):
    db = get_db()
    title = request.form.get('title', '').strip()
    if not title:
        flash('Título é obrigatório.', 'error')
        return redirect(url_for('admin_dashboard'))
    description = request.form.get('description', '')
    short_desc  = request.form.get('short_description', '')
    category_id = request.form.get('category_id') or None
    item_type   = request.form.get('type', 'game')
    version     = request.form.get('version', '')
    mission_en  = 1 if request.form.get('mission_enabled') else 0
    is_feat     = 1 if request.form.get('is_featured') else 0
    feat_order  = int(request.form.get('featured_order') or 0)
    m_rounds    = int(request.form.get('mission_rounds') or 3)
    m_options   = int(request.form.get('mission_options') or 4)

    cover_name = None; file_name = None; file_original = None; file_size = None
    cover = request.files.get('cover')
    if cover and cover.filename:
        ext = cover.filename.rsplit('.', 1)[-1].lower()
        if ext in ALLOWED_IMG:
            cover_name = f"{secrets.token_hex(8)}.{ext}"
            cover.save(os.path.join(IMG_DIR, cover_name))
    f = request.files.get('file')
    if f and f.filename:
        ext = f.filename.rsplit('.', 1)[-1].lower()
        if ext in ALLOWED_FILE:
            file_name = f"{secrets.token_hex(8)}.{ext}"
            path = os.path.join(FILE_DIR, file_name)
            f.save(path)
            file_size = os.path.getsize(path)
            file_original = f.filename

    if item_id:
        db.execute('''UPDATE items SET title=?, description=?, short_description=?,
            category_id=?, type=?, version=?, mission_enabled=?, mission_rounds=?,
            mission_options=?, is_featured=?, featured_order=?,
            cover_image=COALESCE(?, cover_image),
            file_path=COALESCE(?, file_path),
            file_original_name=COALESCE(?, file_original_name),
            file_size=COALESCE(?, file_size) WHERE id=?''',
            (title, description, short_desc, category_id, item_type, version,
             mission_en, m_rounds, m_options, is_feat, feat_order,
             cover_name, file_name, file_original, file_size, item_id))
    else:
        db.execute('''INSERT INTO items (title, description, short_description,
            category_id, type, version, mission_enabled, mission_rounds,
            mission_options, is_featured, featured_order, cover_image,
            file_path, file_original_name, file_size, published_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (title, description, short_desc, category_id, item_type, version,
             mission_en, m_rounds, m_options, is_feat, feat_order,
             cover_name, file_name, file_original, file_size,
             datetime.utcnow().isoformat()))
    db.commit(); db.close()
    flash('Publicação salva com sucesso!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/items/<int:item_id>/delete', methods=['POST'])
@admin_required
def admin_item_delete(item_id):
    db = get_db()
    db.execute('DELETE FROM items WHERE id=?', (item_id,))
    db.commit(); db.close()
    flash('Publicação excluída.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/items/<int:item_id>/toggle-featured', methods=['POST'])
@admin_required
def admin_toggle_featured(item_id):
    db = get_db()
    cur = db.execute('SELECT is_featured FROM items WHERE id=?', (item_id,)).fetchone()
    if cur:
        db.execute('UPDATE items SET is_featured=? WHERE id=?',
                   (0 if cur['is_featured'] else 1, item_id))
        db.commit()
    db.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/categories/create', methods=['POST'])
@admin_required
def admin_category_create():
    name = request.form.get('name', '').strip()
    if name:
        db = get_db()
        try:
            db.execute('INSERT INTO categories (name, slug) VALUES (?,?)',
                       (name, make_slug(name)))
            db.commit()
            flash('Categoria criada.', 'success')
        except sqlite3.IntegrityError:
            flash('Categoria já existe.', 'error')
        db.close()
    return redirect(url_for('admin_dashboard') + '#categories')

@app.route('/admin/categories/<int:cat_id>/delete', methods=['POST'])
@admin_required
def admin_category_delete(cat_id):
    db = get_db()
    db.execute('UPDATE items SET category_id=NULL WHERE category_id=?', (cat_id,))
    db.execute('DELETE FROM categories WHERE id=?', (cat_id,))
    db.commit(); db.close()
    flash('Categoria excluída.', 'success')
    return redirect(url_for('admin_dashboard') + '#categories')

@app.route('/admin/tokens/generate', methods=['POST'])
@admin_required
def admin_token_generate():
    item_id = request.form.get('item_id')
    hours   = int(request.form.get('hours') or 0)
    db = get_db()
    item = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
    if not item:
        db.close()
        flash('Item inválido.', 'error')
        return redirect(url_for('admin_dashboard'))
    token = secrets.token_urlsafe(24)
    expires = (datetime.utcnow() + timedelta(hours=hours)).isoformat() if hours > 0 else None
    db.execute('''INSERT INTO download_tokens (token, item_id, mission_passed, expires_at)
                  VALUES (?,?,1,?)''', (token, item_id, expires))
    db.commit(); db.close()
    flash(f'Link gerado: /d/{token}', 'success')
    return redirect(url_for('admin_dashboard') + '#links')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
