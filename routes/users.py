"""
User Management Module - Admin user CRUD
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from database import execute_query
import uuid

users_bp = Blueprint('users', __name__)

# ============================================================================
# LIST USERS
# ============================================================================

@users_bp.route('/')
@login_required
def list_users():
    """List all users - Tier 1 only"""
    if not current_user.can_manage_users():
        flash('You do not have permission to manage users.', 'danger')
        return redirect(url_for('dashboard'))
    
    query = """
        SELECT 
            user_id,
            username,
            full_name,
            role,
            initials,
            active,
            created_at
        FROM users
        ORDER BY 
            CASE role
                WHEN 'owner' THEN 1
                WHEN 'quality_manager' THEN 2
                WHEN 'operations_manager' THEN 3
                WHEN 'inspector' THEN 4
                WHEN 'machinist' THEN 5
                WHEN 'assembly' THEN 6
                WHEN 'admin' THEN 7
            END,
            full_name
    """
    
    users = execute_query(query, fetch_all=True)
    
    return render_template('users/list.html', users=users)

# ============================================================================
# CREATE USER
# ============================================================================

@users_bp.route('/new', methods=['GET', 'POST'])
@login_required
def create_user():
    """Create a new user"""
    if not current_user.can_manage_users():
        flash('You do not have permission to create users.', 'danger')
        return redirect(url_for('users.list_users'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        role = request.form.get('role', '').strip()
        initials = request.form.get('initials', '').strip().upper()
        active = request.form.get('active') == 'on'
        
        # Validate
        if not username or not password or not full_name or not role:
            flash('Username, password, full name, and role are required.', 'danger')
            return render_template('users/form.html', form_data=request.form)
        
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('users/form.html', form_data=request.form)
        
        # Check for duplicate username
        check_query = "SELECT user_id FROM users WHERE username = %s"
        existing = execute_query(check_query, (username,), fetch_one=True)
        
        if existing:
            flash(f'Username "{username}" already exists.', 'danger')
            return render_template('users/form.html', form_data=request.form)
        
        # Hash password
        password_hash = generate_password_hash(password)
        
        insert_query = """
            INSERT INTO users (
                username, password_hash, full_name, role, 
                initials, active
            ) VALUES (
                %s, %s, %s, %s, %s, %s
            )
            RETURNING user_id
        """
        
        try:
            result = execute_query(
                insert_query,
                (username, password_hash, full_name, role, initials, active),
                fetch_one=True
            )
            
            flash(f'User "{full_name}" created successfully.', 'success')
            return redirect(url_for('users.list_users'))
            
        except Exception as e:
            flash(f'Error creating user: {str(e)}', 'danger')
            return render_template('users/form.html', form_data=request.form)
    
    return render_template('users/form.html', form_data=None)

# ============================================================================
# EDIT USER
# ============================================================================

@users_bp.route('/<user_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    """Edit existing user"""
    if not current_user.can_manage_users():
        flash('You do not have permission to edit users.', 'danger')
        return redirect(url_for('users.list_users'))
    
    query = "SELECT * FROM users WHERE user_id = %s"
    user = execute_query(query, (user_id,), fetch_one=True)
    
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('users.list_users'))
    
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        role = request.form.get('role', '').strip()
        initials = request.form.get('initials', '').strip().upper()
        active = request.form.get('active') == 'on'
        new_password = request.form.get('new_password', '').strip()
        
        if not full_name or not role:
            flash('Full name and role are required.', 'danger')
            return render_template('users/form.html', form_data=request.form, user=user)
        
        if new_password:
            if len(new_password) < 6:
                flash('Password must be at least 6 characters.', 'danger')
                return render_template('users/form.html', form_data=request.form, user=user)
            
            password_hash = generate_password_hash(new_password)
            update_query = """
                UPDATE users SET
                    full_name = %s,
                    role = %s,
                    initials = %s,
                    active = %s,
                    password_hash = %s
                WHERE user_id = %s
            """
            params = (full_name, role, initials, active, password_hash, user_id)
        else:
            update_query = """
                UPDATE users SET
                    full_name = %s,
                    role = %s,
                    initials = %s,
                    active = %s
                WHERE user_id = %s
            """
            params = (full_name, role, initials, active, user_id)
        
        try:
            execute_query(update_query, params)
            flash(f'User "{full_name}" updated successfully.', 'success')
            return redirect(url_for('users.list_users'))
            
        except Exception as e:
            flash(f'Error updating user: {str(e)}', 'danger')
            return render_template('users/form.html', form_data=request.form, user=user)
    
    return render_template('users/form.html', form_data=user, user=user)

# ============================================================================
# TOGGLE ACTIVE STATUS
# ============================================================================

@users_bp.route('/<user_id>/toggle-active', methods=['POST'])
@login_required
def toggle_active(user_id):
    """Toggle user active status"""
    if not current_user.can_manage_users():
        flash('You do not have permission to manage users.', 'danger')
        return redirect(url_for('users.list_users'))
    
    # Don't allow deactivating yourself
    if user_id == str(current_user.user_id):
        flash('You cannot deactivate your own account.', 'danger')
        return redirect(url_for('users.list_users'))
    
    try:
        query = """
            UPDATE users 
            SET active = NOT active 
            WHERE user_id = %s
            RETURNING full_name, active
        """
        result = execute_query(query, (user_id,), fetch_one=True)
        
        status = "activated" if result['active'] else "deactivated"
        flash(f'User "{result["full_name"]}" {status}.', 'success')
        
    except Exception as e:
        flash(f'Error updating user: {str(e)}', 'danger')
    
    return redirect(url_for('users.list_users'))
