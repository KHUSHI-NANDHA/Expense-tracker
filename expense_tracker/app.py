from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, g
from datetime import datetime, timedelta
from functools import wraps
import sqlite3
import os
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Change this to a secure secret key

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_db():
    db = sqlite3.connect('expenses.db', timeout=20)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()
        db.close()

def clean_duplicate_categories(db):
    """Remove duplicate categories for each user"""
    try:
        # Get all users
        users = db.execute('SELECT id FROM user').fetchall()
        
        for user in users:
            user_id = user['id']
            # Get all categories for this user
            categories = db.execute('''
                SELECT id, name 
                FROM category 
                WHERE user_id = ?
                ORDER BY id
            ''', (user_id,)).fetchall()
            
            # Keep track of seen category names
            seen_names = set()
            for category in categories:
                if category['name'] in seen_names:
                    # This is a duplicate, delete it
                    db.execute('DELETE FROM category WHERE id = ?', (category['id'],))
                else:
                    seen_names.add(category['name'])
        
        db.commit()
        print("Cleaned up duplicate categories")
    except Exception as e:
        print(f"Error cleaning up categories: {e}")
        db.rollback()

def migrate_db():
    """Add new columns to existing tables without dropping data"""
    with app.app_context():
        db = get_db()
        try:
            # Check if last_login column exists in user table
            cursor = db.execute("PRAGMA table_info(user)")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'last_login' not in columns:
                db.execute('ALTER TABLE user ADD COLUMN last_login TIMESTAMP')
                print("Added last_login column to user table")
            
            # Clean up duplicate categories
            clean_duplicate_categories(db)
            
            db.commit()
            print("Database migration completed successfully!")
        except Exception as e:
            print(f"Error during migration: {e}")
            db.rollback()
        finally:
            db.close()

def get_user_settings(user_id):
    db = get_db()
    settings = db.execute('SELECT * FROM user_settings WHERE user_id = ?', (user_id,)).fetchone()
    db.close()
    if not settings:
        db = get_db()
        db.execute('INSERT INTO user_settings (user_id) VALUES (?)', (user_id,))
        db.commit()
        settings = db.execute('SELECT * FROM user_settings WHERE user_id = ?', (user_id,)).fetchone()
        db.close()
    return settings

def get_categories(user_id):
    db = get_db()
    categories = db.execute('''
        SELECT * FROM category 
        WHERE user_id = ?
        ORDER BY name
    ''', (user_id,)).fetchall()
    return categories

@app.before_request
def load_user_settings():
    if 'user_id' in session:
        db = get_db()
        settings = db.execute(
            'SELECT * FROM user_settings WHERE user_id = ?',
            (session['user_id'],)
        ).fetchone()
        if not settings:
            # Initialize settings if they don't exist
            db.execute(
                'INSERT INTO user_settings (user_id, theme, currency) VALUES (?, ?, ?)',
                (session['user_id'], 'light', 'USD')
            )
            db.commit()
            settings = db.execute(
                'SELECT * FROM user_settings WHERE user_id = ?',
                (session['user_id'],)
            ).fetchone()
        g.user_settings = dict(settings) if settings else {'theme': 'light', 'currency': 'USD'}
    else:
        g.user_settings = {'theme': 'light', 'currency': 'USD'}

# Routes
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    db = get_db()
    expenses = db.execute('''
        SELECT e.*, c.name as category_name, c.id as category_id
        FROM expense e
        JOIN category c ON e.category_id = c.id
        WHERE e.user_id = ?
        ORDER BY e.date DESC
    ''', (session['user_id'],)).fetchall()
    
    # Calculate total expenses
    total_expenses = sum(expense['amount'] for expense in expenses)
    
    # Get user's settings for balance
    settings = db.execute('SELECT * FROM user_settings WHERE user_id = ?', 
                         (session['user_id'],)).fetchone()
    
    # Calculate balance (if total_balance exists in settings, otherwise use 0)
    initial_balance = settings['total_balance'] if settings and 'total_balance' in settings.keys() else 0
    balance = initial_balance - total_expenses
    
    # Get all categories for the edit modal
    categories = get_categories(session['user_id'])
    
    # Check if user can add expenses
    can_add_expenses = balance > 0
    
    return render_template_with_settings('index.html', 
                         expenses=expenses, 
                         categories=categories,
                         settings=g.user_settings,
                         total_expenses=total_expenses,
                         balance=balance,
                         can_add_expenses=can_add_expenses)

@app.route('/add_expense', methods=['GET', 'POST'])
def add_expense():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Check if user has balance to add expenses
    db = get_db()
    settings = db.execute('SELECT total_balance FROM user_settings WHERE user_id = ?', 
                         (session['user_id'],)).fetchone()
    current_balance = settings['total_balance'] if settings else 0
    
    total_expenses = db.execute('''
        SELECT COALESCE(SUM(amount), 0) as total 
        FROM expense 
        WHERE user_id = ?
    ''', (session['user_id'],)).fetchone()['total']
    
    available_balance = current_balance - total_expenses
    
    if available_balance <= 0:
        flash('You have no balance available to add expenses. Please update your balance first.', 'error')
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        description = request.form['description']
        amount = float(request.form['amount'])
        category_id = int(request.form['category_id'])
        date = request.form['date']
        currency = request.form['currency']
        
        try:
            # Check if there's enough balance
            if available_balance < amount:
                flash('Insufficient balance! Cannot add expense.', 'error')
                return redirect(url_for('index'))
            
            # Update user's preferred currency
            db.execute('UPDATE user_settings SET currency = ? WHERE user_id = ?',
                      (currency, session['user_id']))
            
            # Add the expense
            db.execute('''
                INSERT INTO expense (description, amount, category_id, date, user_id, currency)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (description, amount, category_id, date, session['user_id'], currency))
            db.commit()
            
            flash('Expense added successfully!', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            db.rollback()
            flash(f'Error adding expense: {str(e)}', 'error')
        finally:
            db.close()
    
    categories = get_categories(session['user_id'])
    return render_template_with_settings('add_expense.html', 
                         categories=categories, 
                         settings=g.user_settings)

@app.route('/categories')
def categories():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    categories = get_categories(session['user_id'])
    return render_template_with_settings('categories.html', categories=categories)

@app.route('/add_category', methods=['POST'])
def add_category():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    name = request.form['name']
    
    db = get_db()
    db.execute('''
        INSERT INTO category (name, user_id)
        VALUES (?, ?)
    ''', (name, session['user_id']))
    db.commit()
    
    flash('Category added successfully!', 'success')
    return redirect(url_for('categories'))

@app.route('/analytics')
def analytics():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    db = get_db()
    
    # Get all expenses for the user (for client-side filtering)
    all_expenses = db.execute('''
        SELECT e.*, c.name as category_name
        FROM expense e
        JOIN category c ON e.category_id = c.id
        WHERE e.user_id = ?
        ORDER BY e.date
    ''', (session['user_id'],)).fetchall()
    
    # Convert to list of dictionaries for JSON serialization
    raw_expenses = []
    
    # Predefined colors for categories
    category_colors = {
        'Food': '#FF5733',
        'Transportation': '#33FF57',
        'Entertainment': '#3357FF',
        'Shopping': '#FF33F5',
        'Bills': '#33FFF5',
        'Other': '#808080'
    }
    
    # Create category map with colors
    category_totals = {}
    
    # Process expenses
    for expense in all_expenses:
        exp_dict = dict(expense)
        category_name = exp_dict['category_name']
        
        # Add color to category map
        if category_name not in category_totals:
            category_totals[category_name] = {
                'amount': 0,
                'color': category_colors.get(category_name, '#' + hex(hash(category_name) & 0xFFFFFF)[2:].zfill(6))
            }
        category_totals[category_name]['amount'] += exp_dict['amount']
        
        # Add color to expense for reference
        exp_dict['color'] = category_totals[category_name]['color']
        raw_expenses.append(exp_dict)
    
    # Calculate day of week data
    day_of_week_data = [0] * 7  # Initialize for each day of week
    day_count = [0] * 7  # Count of expenses per day
    
    for expense in all_expenses:
        day_of_week = datetime.strptime(expense['date'], '%Y-%m-%d').weekday()
        day_of_week_data[day_of_week] += expense['amount']
        day_count[day_of_week] += 1
    
    # Calculate average expenses per day of week
    for i in range(7):
        if day_count[i] > 0:
            day_of_week_data[i] = day_of_week_data[i] / day_count[i]
    
    # Get top 5 expenses
    top_expenses = db.execute('''
        SELECT description, amount
        FROM expense
        WHERE user_id = ?
        ORDER BY amount DESC
        LIMIT 5
    ''', (session['user_id'],)).fetchall()
    
    # Convert to dictionaries for JSON serialization
    top_expenses = [dict(expense) for expense in top_expenses]
    
    return render_template_with_settings('analytics.html',
                         raw_expenses=json.dumps(raw_expenses),
                         category_totals=json.dumps(category_totals),
                         day_of_week_data=json.dumps(day_of_week_data),
                         top_expenses=json.dumps(top_expenses))

@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    settings = get_user_settings(session['user_id'])
    return render_template_with_settings('profile.html', settings=settings)

@app.route('/update_settings', methods=['POST'])
def update_settings():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    db = get_db()
    theme = request.form.get('theme')
    currency = request.form.get('currency')
    
    if theme:
        db.execute('''
            INSERT INTO user_settings (user_id, theme) 
            VALUES (?, ?)
            ON CONFLICT(user_id) 
            DO UPDATE SET theme = ?
        ''', (session['user_id'], theme, theme))
        
    if currency:
        db.execute('''
            INSERT INTO user_settings (user_id, currency) 
            VALUES (?, ?)
            ON CONFLICT(user_id) 
            DO UPDATE SET currency = ?
        ''', (session['user_id'], currency, currency))
    
    db.commit()
    return jsonify({'success': True})

@app.route('/change_password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    current_password = request.form['current_password']
    new_password = request.form['new_password']
    
    db = get_db()
    user = db.execute('SELECT * FROM user WHERE id = ? AND password = ?',
                     (session['user_id'], current_password)).fetchone()
    
    if user:
        db.execute('UPDATE user SET password = ? WHERE id = ?',
                  (new_password, session['user_id']))
        db.commit()
        flash('Password updated successfully!', 'success')
    else:
        flash('Current password is incorrect!', 'error')
    
    db.close()
    return redirect(url_for('profile'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        db = get_db()
        user = db.execute('SELECT * FROM user WHERE username = ? AND password = ?',
                         (username, password)).fetchone()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            # Update last login time
            db.execute('UPDATE user SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (user['id'],))
            db.commit()
            # Track login activity
            track_user_activity(user['id'], 'login')
            # Initialize user settings if not exists
            get_user_settings(user['id'])
            return redirect(url_for('index'))
        flash('Invalid username or password', 'error')
        db.close()
    
    return render_template_with_settings('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        initial_balance = float(request.form['initial_balance'])
        
        db = get_db()
        try:
            # Create user
            cursor = db.execute('INSERT INTO user (username, password, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)',
                              (username, password))
            user_id = cursor.lastrowid
            
            # Track registration activity using the same db connection
            track_user_activity(user_id, 'register', db)
            
            # Create user settings with initial balance
            db.execute('''
                INSERT INTO user_settings (user_id, total_balance, theme, currency)
                VALUES (?, ?, 'light', 'USD')
            ''', (user_id, initial_balance))
            
            # Create default categories for the user
            default_categories = [
                'Food', 'Transportation', 'Entertainment',
                'Shopping', 'Bills', 'Other'
            ]
            for category in default_categories:
                db.execute('INSERT INTO category (name, user_id) VALUES (?, ?)',
                          (category, user_id))
            
            db.commit()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            db.rollback()
            flash('Username already exists!', 'error')
        finally:
            db.close()
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/update_balance', methods=['POST'])
def update_balance():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    try:
        new_balance = float(request.form.get('total_balance', 0))
        db = get_db()
        db.execute('''
            UPDATE user_settings 
            SET total_balance = ? 
            WHERE user_id = ?
        ''', (new_balance, session['user_id']))
        db.commit()
        return jsonify({'success': True})
    except ValueError:
        return jsonify({'error': 'Invalid balance value'}), 400

@app.route('/delete_expense/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    try:
        db = get_db()
        # First get the expense amount to update the balance
        expense = db.execute(
            'SELECT amount FROM expense WHERE id = ? AND user_id = ?',
            (expense_id, session['user_id'])
        ).fetchone()
        
        if expense:
            # Delete the expense
            db.execute(
                'DELETE FROM expense WHERE id = ? AND user_id = ?',
                (expense_id, session['user_id'])
            )
            db.commit()
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Expense not found'})
    except Exception as e:
        print(f"Error deleting expense: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/update_expense_category/<int:expense_id>', methods=['POST'])
@login_required
def update_expense_category(expense_id):
    try:
        data = request.get_json()
        category_id = data.get('category_id')
        
        if not category_id:
            return jsonify({'success': False, 'error': 'Category ID is required'})
            
        db = get_db()
        # Verify the expense belongs to the user and update its category
        result = db.execute('''
            UPDATE expense 
            SET category_id = ?
            WHERE id = ? AND user_id = ?
            ''', (category_id, expense_id, session['user_id']))
        
        if result.rowcount > 0:
            db.commit()
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Expense not found or unauthorized'})
    except Exception as e:
        print(f"Error updating expense category: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/stats')
@login_required
def stats():
    if session.get('username') != 'admin':  # Only allow admin to view stats
        return redirect(url_for('index'))
    
    stats = get_user_stats()
    return render_template_with_settings('stats.html', stats=stats)

@app.route('/update_expense/<int:expense_id>', methods=['POST'])
@login_required
def update_expense(expense_id):
    try:
        data = request.get_json()
        category_id = data.get('category_id')
        amount = data.get('amount')
        date = data.get('date')
        
        if not all([category_id, amount is not None, date]):
            return jsonify({'success': False, 'error': 'Category ID, amount, and date are required'})
            
        db = get_db()
        
        # Get the old expense amount
        old_expense = db.execute('''
            SELECT amount FROM expense 
            WHERE id = ? AND user_id = ?
        ''', (expense_id, session['user_id'])).fetchone()
        
        if not old_expense:
            return jsonify({'success': False, 'error': 'Expense not found or unauthorized'})
        
        # Calculate the difference in amount
        amount_diff = amount - old_expense['amount']
        
        # Get current balance
        settings = db.execute('SELECT total_balance FROM user_settings WHERE user_id = ?', 
                            (session['user_id'],)).fetchone()
        current_balance = settings['total_balance'] if settings else 0
        
        # Calculate total expenses excluding the current expense
        total_expenses = db.execute('''
            SELECT COALESCE(SUM(amount), 0) as total 
            FROM expense 
            WHERE user_id = ? AND id != ?
        ''', (session['user_id'], expense_id)).fetchone()['total']
        
        # Check if the new total would exceed the balance
        if total_expenses + amount > current_balance:
            return jsonify({
                'success': False, 
                'error': 'This change would exceed your available balance'
            })
        
        # Update the expense
        result = db.execute('''
            UPDATE expense 
            SET category_id = ?, amount = ?, date = ?
            WHERE id = ? AND user_id = ?
        ''', (category_id, amount, date, expense_id, session['user_id']))
        
        if result.rowcount > 0:
            db.commit()
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Failed to update expense'})
    except Exception as e:
        print(f"Error updating expense: {e}")
        return jsonify({'success': False, 'error': str(e)})

def track_user_activity(user_id, activity_type, db=None):
    should_close = False
    if db is None:
        db = get_db()
        should_close = True
    try:
        db.execute('''
            INSERT INTO user_activity (user_id, activity_type)
            VALUES (?, ?)
        ''', (user_id, activity_type))
        db.commit()
    finally:
        if should_close:
            db.close()

def get_user_stats():
    db = get_db()
    # Get total registered users
    total_users = db.execute('SELECT COUNT(*) as count FROM user').fetchone()['count']
    
    # Get users active in last 24 hours
    active_users = db.execute('''
        SELECT COUNT(DISTINCT user_id) as count 
        FROM user_activity 
        WHERE timestamp > datetime('now', '-1 day')
    ''').fetchone()['count']
    
    # Get users active in last 5 minutes
    current_users = db.execute('''
        SELECT COUNT(DISTINCT user_id) as count 
        FROM user_activity 
        WHERE timestamp > datetime('now', '-5 minutes')
    ''').fetchone()['count']
    
    # Get new users in last 24 hours
    new_users = db.execute('''
        SELECT COUNT(*) as count 
        FROM user 
        WHERE created_at > datetime('now', '-1 day')
    ''').fetchone()['count']
    
    db.close()
    return {
        'total_users': total_users,
        'active_users': active_users,
        'current_users': current_users,
        'new_users': new_users
    }

def render_template_with_settings(*args, **kwargs):
    if 'settings' not in kwargs:
        kwargs['settings'] = g.user_settings
    return render_template(*args, **kwargs)

# Update all route functions to use render_template_with_settings instead of render_template
app.render_template = render_template_with_settings

if __name__ == '__main__':
    if not os.path.exists('expenses.db'):
        init_db()
    else:
        migrate_db()
    app.run(debug=True) 