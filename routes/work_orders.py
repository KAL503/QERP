"""
Work Orders Module - Digital Traveler and Production Control
ISO 9001: 8.1, 8.5.1, 8.5.2
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from database import execute_query, get_db_cursor
from datetime import date, datetime
import uuid

work_orders_bp = Blueprint('work_orders', __name__)

# ============================================================================
# WORK ORDER LIST / DASHBOARD
# ============================================================================

@work_orders_bp.route('/')
@login_required
def list_work_orders():
    """List all work orders with filtering"""
    # Get filter parameters
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '').strip()
    customer_id = request.args.get('customer_id', '').strip()
    
    # Build query
    query = """
        SELECT 
            wo.work_order_id,
            wo.work_order_number,
            wo.status,
            wo.quantity_ordered,
            wo.quantity_completed,
            wo.production_due_date,
            wo.created_at,
            c.customer_code,
            c.company_name,
            p.customer_part_number,
            pr.revision_level,
            wo.fai_required,
            wo.aql_required,
            (SELECT COUNT(*) FROM ncrs WHERE work_order_id = wo.work_order_id AND status != 'closed') as open_ncr_count
        FROM work_orders wo
        JOIN customers c ON wo.customer_id = c.customer_id
        JOIN parts p ON wo.part_id = p.part_id
        JOIN part_revisions pr ON wo.revision_id = pr.revision_id
        WHERE 1=1
    """
    params = []
    
    if search:
        query += """ AND (
            LOWER(wo.work_order_number) LIKE LOWER(%s) OR
            LOWER(p.customer_part_number) LIKE LOWER(%s) OR
            LOWER(wo.customer_po_number) LIKE LOWER(%s)
        )"""
        search_pattern = f'%{search}%'
        params.extend([search_pattern, search_pattern, search_pattern])
    
    if status:
        query += " AND wo.status = %s"
        params.append(status)
    
    if customer_id:
        query += " AND wo.customer_id = %s"
        params.append(customer_id)
    
    query += " ORDER BY wo.created_at DESC"
    
    work_orders = execute_query(query, tuple(params) if params else None, fetch_all=True)
    
    # Get customers for filter
    customers_query = """
        SELECT customer_id, customer_code, company_name
        FROM customers
        WHERE active = TRUE
        ORDER BY company_name
    """
    customers = execute_query(customers_query, fetch_all=True)
    
    # Get status counts for dashboard
    status_counts_query = """
        SELECT status, COUNT(*) as count
        FROM work_orders
        GROUP BY status
    """
    status_counts = execute_query(status_counts_query, fetch_all=True)
    status_dict = {row['status']: row['count'] for row in status_counts} if status_counts else {}
    
    return render_template(
        'work_orders/list.html',
        work_orders=work_orders,
        customers=customers,
        search=search,
        selected_status=status,
        selected_customer_id=customer_id,
        status_counts=status_dict
    )

# ============================================================================
# VIEW WORK ORDER DETAIL
# ============================================================================

@work_orders_bp.route('/<work_order_id>')
@login_required
def view_work_order(work_order_id):
    """View work order details with routing and material certs"""
    # Get work order details
    query = """
        SELECT 
            wo.*,
            c.customer_code,
            c.company_name,
            c.address_line1,
            c.city,
            c.state,
            p.customer_part_number,
            p.description as part_description,
            p.material,
            p.finish,
            pr.revision_level,
            pr.drawing_file_path,
            u_created.full_name as created_by_name,
            u_released.full_name as released_by_name,
            u_closed.full_name as closed_by_name
        FROM work_orders wo
        JOIN customers c ON wo.customer_id = c.customer_id
        JOIN parts p ON wo.part_id = p.part_id
        JOIN part_revisions pr ON wo.revision_id = pr.revision_id
        LEFT JOIN users u_created ON wo.created_by = u_created.user_id
        LEFT JOIN users u_released ON wo.released_by = u_released.user_id
        LEFT JOIN users u_closed ON wo.closed_by = u_closed.user_id
        WHERE wo.work_order_id = %s
    """
    wo = execute_query(query, (work_order_id,), fetch_one=True)
    
    if not wo:
        flash('Work order not found.', 'danger')
        return redirect(url_for('work_orders.list_work_orders'))
    
    # Get routing operations
    operations_query = """
        SELECT 
            wop.*,
            u_start.full_name as started_by_name,
            u_start.initials as started_by_initials,
            u_end.full_name as ended_by_name,
            u_end.initials as ended_by_initials,
            ospo.po_number,
            ospo.status as os_status
        FROM work_order_operations wop
        LEFT JOIN users u_start ON wop.start_by = u_start.user_id
        LEFT JOIN users u_end ON wop.end_by = u_end.user_id
        LEFT JOIN outside_service_pos ospo ON wop.outside_service_po_id = ospo.os_po_id
        WHERE wop.work_order_id = %s
        ORDER BY wop.stream_id, wop.sequence_number
    """
    operations = execute_query(operations_query, (work_order_id,), fetch_all=True)
    
    # Get material certs
    certs_query = """
        SELECT mc.*, u.full_name as entered_by_name
        FROM material_certs mc
        LEFT JOIN users u ON mc.entered_by = u.user_id
        WHERE mc.work_order_id = %s
        ORDER BY mc.entered_at DESC
    """
    material_certs = execute_query(certs_query, (work_order_id,), fetch_all=True)
    
    # Get open NCRs
    ncrs_query = """
        SELECT ncr_id, ncr_number, status, description, quantity_nonconforming
        FROM ncrs
        WHERE work_order_id = %s AND status != 'closed'
        ORDER BY initiated_at DESC
    """
    open_ncrs = execute_query(ncrs_query, (work_order_id,), fetch_all=True)
    
    # Check if can release to floor
    can_release_result = check_can_release_to_floor(work_order_id, wo, material_certs)
    
    return render_template(
        'work_orders/view.html',
        wo=wo,
        operations=operations,
        material_certs=material_certs,
        open_ncrs=open_ncrs,
        can_release=can_release_result
    )

# ============================================================================
# CREATE NEW WORK ORDER
# ============================================================================

@work_orders_bp.route('/new', methods=['GET', 'POST'])
@login_required
def create_work_order():
    """Create a new work order"""
    if not current_user.can_create_work_orders():
        flash('You do not have permission to create work orders.', 'danger')
        return redirect(url_for('work_orders.list_work_orders'))
    
    if request.method == 'POST':
        # Get form data
        customer_id = request.form.get('customer_id', '').strip()
        part_id = request.form.get('part_id', '').strip()
        revision_id = request.form.get('revision_id', '').strip()
        customer_po_number = request.form.get('customer_po_number', '').strip()
        customer_po_line = request.form.get('customer_po_line', '').strip()
        customer_po_date = request.form.get('customer_po_date', '').strip()
        quantity_ordered = request.form.get('quantity_ordered', '').strip()
        production_due_date = request.form.get('production_due_date', '').strip()
        fai_required = request.form.get('fai_required') == 'on'
        aql_required = request.form.get('aql_required') == 'on'
        special_fa_required = request.form.get('special_fa_required') == 'on'
        notes = request.form.get('notes', '').strip()
        
        # Validate required fields
        if not all([customer_id, part_id, revision_id, quantity_ordered, production_due_date]):
            flash('Customer, part, revision, quantity, and due date are required.', 'danger')
            return render_template('work_orders/form.html', 
                                 form_data=request.form,
                                 customers=get_customers(),
                                 parts=[],
                                 revisions=[])
        
        try:
            quantity_ordered = int(quantity_ordered)
            if quantity_ordered <= 0:
                raise ValueError("Quantity must be positive")
        except ValueError:
            flash('Quantity ordered must be a positive number.', 'danger')
            return render_template('work_orders/form.html',
                                 form_data=request.form,
                                 customers=get_customers(),
                                 parts=get_parts_for_customer(customer_id),
                                 revisions=get_revisions_for_part(part_id))
        
        # Generate work order number (MMDDYYYY-SEQ)
        wo_number = generate_work_order_number()
        
        # Insert work order
        insert_query = """
            INSERT INTO work_orders (
                work_order_number, customer_id, part_id, revision_id,
                customer_po_number, customer_po_line, customer_po_date,
                quantity_ordered, production_due_date,
                fai_required, aql_required, special_fa_required,
                notes, status, created_by, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, CURRENT_TIMESTAMP
            )
            RETURNING work_order_id
        """
        
        try:
            result = execute_query(
                insert_query,
                (wo_number, customer_id, part_id, revision_id,
                 customer_po_number, customer_po_line, customer_po_date,
                 quantity_ordered, production_due_date,
                 fai_required, aql_required, special_fa_required,
                 notes, current_user.user_id),
                fetch_one=True
            )
            
            flash(f'Work order {wo_number} created successfully.', 'success')
            return redirect(url_for('work_orders.view_work_order', work_order_id=result['work_order_id']))
            
        except Exception as e:
            flash(f'Error creating work order: {str(e)}', 'danger')
            return render_template('work_orders/form.html',
                                 form_data=request.form,
                                 customers=get_customers(),
                                 parts=get_parts_for_customer(customer_id),
                                 revisions=get_revisions_for_part(part_id))
    
    # GET request - show form
    return render_template('work_orders/form.html',
                         form_data=None,
                         customers=get_customers(),
                         parts=[],
                         revisions=[])

# ============================================================================
# EDIT WORK ORDER
# ============================================================================

@work_orders_bp.route('/<work_order_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_work_order(work_order_id):
    """Edit existing work order - allowed even if released to floor (managers only)"""
    
    # Permission check - only managers can edit
    if not current_user.is_operations_manager():
        flash('Only operations managers and above can edit work orders.', 'danger')
        return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))
    
    # Get existing work order
    wo_query = """
        SELECT wo.*, c.customer_code, c.company_name, 
               p.customer_part_number, pr.revision_level
        FROM work_orders wo
        JOIN customers c ON wo.customer_id = c.customer_id
        JOIN parts p ON wo.part_id = p.part_id
        JOIN part_revisions pr ON wo.revision_id = pr.revision_id
        WHERE wo.work_order_id = %s
    """
    wo = execute_query(wo_query, (work_order_id,), fetch_one=True)
    
    if not wo:
        flash('Work order not found.', 'danger')
        return redirect(url_for('work_orders.list_work_orders'))
    
    if request.method == 'POST':
        # Get form data
        customer_po_number = request.form.get('customer_po_number', '').strip()
        customer_po_line = request.form.get('customer_po_line', '').strip()
        customer_po_date = request.form.get('customer_po_date', '').strip()
        quantity_ordered = request.form.get('quantity_ordered', '').strip()
        production_due_date = request.form.get('production_due_date', '').strip()
        fai_required = request.form.get('fai_required') == 'on'
        aql_required = request.form.get('aql_required') == 'on'
        special_fa_required = request.form.get('special_fa_required') == 'on'
        notes = request.form.get('notes', '').strip()
        
        # Validate
        if not all([quantity_ordered, production_due_date]):
            flash('Quantity and due date are required.', 'danger')
            return redirect(url_for('work_orders.edit_work_order', work_order_id=work_order_id))
        
        try:
            quantity_ordered = int(quantity_ordered)
            if quantity_ordered <= 0:
                raise ValueError()
        except ValueError:
            flash('Quantity ordered must be a positive number.', 'danger')
            return redirect(url_for('work_orders.edit_work_order', work_order_id=work_order_id))
        
        # Update work order
        update_query = """
            UPDATE work_orders
            SET customer_po_number = %s,
                customer_po_line = %s,
                customer_po_date = %s,
                quantity_ordered = %s,
                production_due_date = %s,
                fai_required = %s,
                aql_required = %s,
                special_fa_required = %s,
                notes = %s
            WHERE work_order_id = %s
        """
        
        try:
            execute_query(
                update_query,
                (customer_po_number, customer_po_line, customer_po_date,
                 quantity_ordered, production_due_date,
                 fai_required, aql_required, special_fa_required,
                 notes, work_order_id)
            )
            
            flash(f'Work order {wo["work_order_number"]} updated successfully.', 'success')
            
            # Log the change if WO is active on floor
            if wo['status'] not in ('draft', 'pending_release'):
                flash('Note: This work order is active on the shop floor. Changes may affect current operations.', 'warning')
            
            return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))
            
        except Exception as e:
            flash(f'Error updating work order: {str(e)}', 'danger')
            return redirect(url_for('work_orders.edit_work_order', work_order_id=work_order_id))
    
    # GET - show form with existing data
    return render_template('work_orders/edit_form.html', wo=wo)

# ============================================================================
# ROUTING BUILDER
# ============================================================================

@work_orders_bp.route('/<work_order_id>/routing', methods=['GET', 'POST'])
@login_required
def edit_routing(work_order_id):
    """Edit work order routing (add/edit/delete operations) - allowed even after release"""
    if not current_user.can_create_work_orders():
        flash('You do not have permission to edit routing.', 'danger')
        return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))
    
    # Get work order
    wo_query = """
        SELECT wo.*, p.customer_part_number, c.company_name
        FROM work_orders wo
        JOIN parts p ON wo.part_id = p.part_id
        JOIN customers c ON wo.customer_id = c.customer_id
        WHERE wo.work_order_id = %s
    """
    wo = execute_query(wo_query, (work_order_id,), fetch_one=True)
    
    if not wo:
        flash('Work order not found.', 'danger')
        return redirect(url_for('work_orders.list_work_orders'))
    
    # Warning if WO is active (but still allow editing)
    if wo['status'] not in ('draft', 'pending_release'):
        flash('Warning: This work order is active on the shop floor. Changes to routing may affect operations in progress.', 'warning')
    
    # Get current operations
    ops_query = """
        SELECT *
        FROM work_order_operations
        WHERE work_order_id = %s
        ORDER BY stream_id, sequence_number
    """
    operations = execute_query(ops_query, (work_order_id,), fetch_all=True)
    
    # Get standard operation codes
    operation_codes = get_standard_operation_codes()
    
    # Check if part has standard operations
    standard_ops = get_standard_operations_for_part(wo['part_id'])
    
    return render_template(
        'work_orders/routing.html',
        wo=wo,
        operations=operations,
        operation_codes=operation_codes,
        standard_ops_available=len(standard_ops) > 0 if standard_ops else False,
        standard_ops_count=len(standard_ops) if standard_ops else 0
    )

@work_orders_bp.route('/<work_order_id>/routing/add', methods=['POST'])
@login_required
def add_operation(work_order_id):
    """Add a new operation to routing"""
    if not current_user.can_create_work_orders():
        return jsonify({'error': 'Permission denied'}), 403
    
    data = request.get_json()
    operation_code = data.get('operation_code')
    operation_description = data.get('operation_description')
    operation_type = data.get('operation_type')
    sequence_number = data.get('sequence_number', 10)
    stream_id = data.get('stream_id', 1)
    work_center = data.get('work_center', '')
    
    if not all([operation_code, operation_description, operation_type]):
        return jsonify({'error': 'Missing required fields'}), 400
    
    insert_query = """
        INSERT INTO work_order_operations (
            work_order_id, stream_id, sequence_number,
            operation_code, operation_description, operation_type,
            work_center, status
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, 'pending'
        )
        RETURNING operation_id
    """
    
    try:
        result = execute_query(
            insert_query,
            (work_order_id, stream_id, sequence_number,
             operation_code, operation_description, operation_type,
             work_center),
            fetch_one=True
        )
        return jsonify({'success': True, 'operation_id': str(result['operation_id'])})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================================
# MATERIAL CERT MANAGEMENT
# ============================================================================

@work_orders_bp.route('/<work_order_id>/certs/add', methods=['GET', 'POST'])
@login_required
def add_material_cert(work_order_id):
    """Add material certification"""
    if not current_user.can_create_work_orders():
        flash('You do not have permission to add material certs.', 'danger')
        return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))
    
    wo_query = "SELECT work_order_number FROM work_orders WHERE work_order_id = %s"
    wo = execute_query(wo_query, (work_order_id,), fetch_one=True)
    
    if not wo:
        flash('Work order not found.', 'danger')
        return redirect(url_for('work_orders.list_work_orders'))
    
    if request.method == 'POST':
        part_number = request.form.get('part_number_on_cert', '').strip()
        description = request.form.get('description', '').strip()
        lot_number = request.form.get('lot_number', '').strip()
        heat_number = request.form.get('heat_number', '').strip()
        cert_number = request.form.get('certification_number', '').strip()
        manufacturer = request.form.get('manufacturer', '').strip()
        cert_file_path = request.form.get('cert_file_path', '').strip()
        
        if not cert_file_path:
            flash('Certificate file path is required.', 'danger')
            return render_template('work_orders/cert_form.html', wo=wo, form_data=request.form)
        
        insert_query = """
            INSERT INTO material_certs (
                work_order_id, part_number_on_cert, description,
                lot_number, heat_number, certification_number,
                manufacturer, cert_file_path,
                entered_by, entered_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
            )
        """
        
        try:
            execute_query(
                insert_query,
                (work_order_id, part_number, description,
                 lot_number, heat_number, cert_number,
                 manufacturer, cert_file_path,
                 current_user.user_id)
            )
            flash('Material certification added successfully.', 'success')
            return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))
        except Exception as e:
            flash(f'Error adding cert: {str(e)}', 'danger')
            return render_template('work_orders/cert_form.html', wo=wo, form_data=request.form)
    
    return render_template('work_orders/cert_form.html', wo=wo, form_data=None)

# ============================================================================
# RELEASE TO FLOOR
# ============================================================================

@work_orders_bp.route('/<work_order_id>/release', methods=['POST'])
@login_required
def release_to_floor(work_order_id):
    """Release work order to production floor"""
    if not current_user.is_operations_manager():
        flash('Only operations managers and above can release work orders.', 'danger')
        return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))
    
    # Get work order
    wo_query = "SELECT * FROM work_orders WHERE work_order_id = %s"
    wo = execute_query(wo_query, (work_order_id,), fetch_one=True)
    
    if not wo:
        flash('Work order not found.', 'danger')
        return redirect(url_for('work_orders.list_work_orders'))
    
    # Get material certs
    certs_query = "SELECT * FROM material_certs WHERE work_order_id = %s"
    certs = execute_query(certs_query, (work_order_id,), fetch_all=True)
    
    # Check release requirements
    can_release, errors, warnings = check_can_release_to_floor(work_order_id, wo, certs)
    
    if not can_release:
        for error in errors:
            flash(error, 'danger')
        return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))
    
    # Show warnings but proceed
    for warning in warnings:
        flash(warning, 'warning')
    
    # Release to floor
    update_query = """
        UPDATE work_orders
        SET status = 'released_to_floor', released_by = %s, released_at = CURRENT_TIMESTAMP
        WHERE work_order_id = %s
    """
    
    try:
        execute_query(update_query, (current_user.user_id, work_order_id))
        flash(f'Work order {wo["work_order_number"]} released to floor.', 'success')
    except Exception as e:
        flash(f'Error releasing work order: {str(e)}', 'danger')
    
    return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def generate_work_order_number():
    """Generate work order number in format MMDDYYYY-SEQ"""
    today = date.today()
    date_prefix = today.strftime('%m%d%Y')
    
    # Get highest sequence for today
    query = """
        SELECT work_order_number
        FROM work_orders
        WHERE work_order_number LIKE %s
        ORDER BY work_order_number DESC
        LIMIT 1
    """
    result = execute_query(query, (f'{date_prefix}-%',), fetch_one=True)
    
    if result:
        last_number = result['work_order_number']
        last_seq = int(last_number.split('-')[1])
        new_seq = last_seq + 1
    else:
        new_seq = 1
    
    return f'{date_prefix}-{new_seq:03d}'

def get_customers():
    """Get all active customers"""
    query = """
        SELECT customer_id, customer_code, company_name
        FROM customers
        WHERE active = TRUE
        ORDER BY company_name
    """
    return execute_query(query, fetch_all=True)

def get_parts_for_customer(customer_id):
    """Get parts for a specific customer (includes parts where customer is primary OR additional)"""
    if not customer_id:
        return []
    query = """
        SELECT DISTINCT p.part_id, p.customer_part_number, p.description
        FROM parts p
        LEFT JOIN part_customers pc ON p.part_id = pc.part_id
        WHERE (p.customer_id = %s OR pc.customer_id = %s)
          AND p.active = TRUE
        ORDER BY p.customer_part_number
    """
    return execute_query(query, (customer_id, customer_id), fetch_all=True)

def get_revisions_for_part(part_id):
    """Get revisions for a specific part"""
    if not part_id:
        return []
    query = """
        SELECT revision_id, revision_level, effective_date, superseded_date
        FROM part_revisions
        WHERE part_id = %s
        ORDER BY effective_date DESC
    """
    return execute_query(query, (part_id,), fetch_all=True)

def get_standard_operation_codes():
    """Return standard operation codes from traveler legend"""
    return [
        {'code': 'Op-M0', 'desc': 'Set Up', 'type': 'machining'},
        {'code': 'Op-M1', 'desc': 'Saw', 'type': 'machining'},
        {'code': 'Op-M2', 'desc': 'Unload', 'type': 'machining'},
        {'code': 'Op-M3', 'desc': 'Mill/Lathe', 'type': 'machining'},
        {'code': 'Op-M4', 'desc': 'Deburr', 'type': 'machining'},
        {'code': 'Op-M5', 'desc': 'Hardware Install', 'type': 'assembly'},
        {'code': 'Op-M6', 'desc': 'Install Dowel Pins', 'type': 'assembly'},
        {'code': 'Op-M8', 'desc': 'Install Helicoil', 'type': 'assembly'},
        {'code': 'Op-F2', 'desc': 'Anodize', 'type': 'finishing'},
        {'code': 'Op-F3', 'desc': 'Electropolish', 'type': 'finishing'},
        {'code': 'Op-F4', 'desc': 'Teflon', 'type': 'finishing'},
        {'code': 'Op-F5', 'desc': 'Zinc', 'type': 'finishing'},
        {'code': 'Op-F6', 'desc': 'Electroless Nickel', 'type': 'finishing'},
        {'code': 'Op-Q1', 'desc': 'In-Process Inspection', 'type': 'quality'},
        {'code': 'Op-Q2', 'desc': 'Finishing Inspection', 'type': 'quality'},
        {'code': 'Op-Q3', 'desc': 'Final Inspection', 'type': 'quality'},
        {'code': 'Op-A6', 'desc': 'Bag & Tag', 'type': 'assembly'},
        {'code': 'Op-A7', 'desc': 'Box & Ship', 'type': 'assembly'},
        {'code': 'OS', 'desc': 'Outside Service', 'type': 'outside_service'},
    ]

def check_can_release_to_floor(work_order_id, wo, material_certs):
    """
    Check if work order can be released to floor
    Requirements:
    1. Status is 'draft' or 'pending_release'
    2. Production due date is set
    3. At least one operation exists in routing
    
    NOTE: Material certs are NOT required for release (they come with order, scanned by AP later)
    
    Returns: (can_release: bool, errors: list, warnings: list)
    """
    errors = []
    warnings = []
    
    if wo['status'] not in ('draft', 'pending_release'):
        errors.append('Work order must be in draft or pending release status.')
    
    if not wo['production_due_date']:
        errors.append('Production due date is required.')
    
    # Warning (not blocking) if no material certs
    if not material_certs or len(material_certs) == 0:
        warnings.append('No material certifications attached. Ensure AP scans cert when invoice is processed.')
    
    # Check for operations
    ops_query = "SELECT COUNT(*) as count FROM work_order_operations WHERE work_order_id = %s"
    ops_count = execute_query(ops_query, (work_order_id,), fetch_one=True)
    if not ops_count or ops_count['count'] == 0:
        errors.append('At least one operation must be added to the routing.')
    
    return (len(errors) == 0, errors, warnings)

# ============================================================================
# AJAX ENDPOINTS FOR DYNAMIC DROPDOWNS
# ============================================================================

@work_orders_bp.route('/api/parts/<customer_id>')
@login_required
def api_get_parts(customer_id):
    """API endpoint to get parts for a customer"""
    parts = get_parts_for_customer(customer_id)
    return jsonify([{
        'part_id': str(p['part_id']),
        'customer_part_number': p['customer_part_number'],
        'description': p['description'] or ''
    } for p in parts])

@work_orders_bp.route('/api/revisions/<part_id>')
@login_required
def api_get_revisions(part_id):
    """API endpoint to get revisions for a part"""
    revisions = get_revisions_for_part(part_id)
    return jsonify([{
        'revision_id': str(r['revision_id']),
        'revision_level': r['revision_level'],
        'effective_date': str(r['effective_date']),
        'is_current': r['superseded_date'] is None
    } for r in revisions])

# ============================================================================
# ROUTING REMOVE AND RESEQUENCE
# ============================================================================

@work_orders_bp.route('/<work_order_id>/routing/remove/<operation_id>', methods=['POST'])
@login_required
def remove_operation(work_order_id, operation_id):
    """Remove an operation from routing (only if pending)"""
    if not current_user.can_create_work_orders():
        return jsonify({'error': 'Permission denied'}), 403
    
    # Check operation status
    op_query = "SELECT status FROM work_order_operations WHERE operation_id = %s"
    op = execute_query(op_query, (operation_id,), fetch_one=True)
    
    if not op:
        return jsonify({'error': 'Operation not found'}), 404
    
    if op['status'] not in ('pending', 'on_hold'):
        return jsonify({'error': f'Cannot delete {op["status"]} operation. Only pending operations can be deleted.'}), 400
    
    try:
        execute_query(
            "DELETE FROM work_order_operations WHERE operation_id = %s AND work_order_id = %s",
            (operation_id, work_order_id)
        )
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@work_orders_bp.route('/<work_order_id>/routing/resequence/<operation_id>', methods=['POST'])
@login_required
def resequence_operation(work_order_id, operation_id):
    """Update the sequence number of a single operation"""
    if not current_user.can_create_work_orders():
        return jsonify({'error': 'Permission denied'}), 403
    data = request.get_json()
    seq = data.get('sequence_number')
    if not seq:
        return jsonify({'error': 'sequence_number required'}), 400
    try:
        execute_query(
            "UPDATE work_order_operations SET sequence_number = %s WHERE operation_id = %s AND work_order_id = %s",
            (seq, operation_id, work_order_id)
        )
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================================
# STANDARD ROUTING FUNCTIONS
# ============================================================================

def get_standard_operations_for_part(part_id):
    """Get saved standard operations for a part"""
    query = """
        SELECT * FROM part_standard_operations 
        WHERE part_id = %s 
        ORDER BY sequence_number
    """
    return execute_query(query, (part_id,), fetch_all=True)

@work_orders_bp.route('/<work_order_id>/save-standard-routing', methods=['POST'])
@login_required
def save_standard_routing(work_order_id):
    """Save current WO operations as standard routing for this part"""
    if not current_user.can_create_work_orders():
        flash('You do not have permission to save standard routings.', 'danger')
        return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))
    
    # Get work order and part info
    wo_query = """
        SELECT wo.part_id, p.customer_part_number
        FROM work_orders wo
        JOIN parts p ON wo.part_id = p.part_id
        WHERE wo.work_order_id = %s
    """
    wo = execute_query(wo_query, (work_order_id,), fetch_one=True)
    
    if not wo:
        flash('Work order not found.', 'danger')
        return redirect(url_for('work_orders.list_work_orders'))
    
    # Get current operations for this WO
    ops_query = """
        SELECT * FROM work_order_operations
        WHERE work_order_id = %s
        ORDER BY stream_id, sequence_number
    """
    operations = execute_query(ops_query, (work_order_id,), fetch_all=True)
    
    if not operations:
        flash('No operations to save as standard routing.', 'warning')
        return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))
    
    try:
        # Delete existing standard operations for this part
        execute_query(
            "DELETE FROM part_standard_operations WHERE part_id = %s",
            (wo['part_id'],)
        )
        
        # Insert new standard operations
        insert_query = """
            INSERT INTO part_standard_operations (
                part_id, sequence_number, operation_code, operation_description,
                operation_type, work_center, machine_number_planned, notes, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        for op in operations:
            execute_query(
                insert_query,
                (wo['part_id'], op['sequence_number'], op['operation_code'],
                 op['operation_description'], op['operation_type'],
                 op['work_center'], op['machine_number_planned'],
                 op['notes'], current_user.user_id)
            )
        
        flash(f'Standard routing saved for {wo["customer_part_number"]} ({len(operations)} operations)', 'success')
        return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))
        
    except Exception as e:
        flash(f'Error saving standard routing: {str(e)}', 'danger')
        return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))

@work_orders_bp.route('/<work_order_id>/load-standard-routing', methods=['POST'])
@login_required
def load_standard_routing(work_order_id):
    """Load standard routing operations into this work order"""
    if not current_user.can_create_work_orders():
        flash('You do not have permission to load standard routings.', 'danger')
        return redirect(url_for('work_orders.view_work_order', work_order_id=work_order_id))
    
    # Get work order and part info
    wo_query = """
        SELECT wo.part_id, wo.quantity_ordered
        FROM work_orders wo
        WHERE wo.work_order_id = %s
    """
    wo = execute_query(wo_query, (work_order_id,), fetch_one=True)
    
    if not wo:
        flash('Work order not found.', 'danger')
        return redirect(url_for('work_orders.list_work_orders'))
    
    # Get standard operations for this part
    standard_ops = get_standard_operations_for_part(wo['part_id'])
    
    if not standard_ops:
        flash('No standard routing defined for this part.', 'warning')
        return redirect(url_for('work_orders.edit_routing', work_order_id=work_order_id))
    
    try:
        # Insert operations from standard routing
        insert_query = """
            INSERT INTO work_order_operations (
                work_order_id, stream_id, sequence_number, operation_code,
                operation_description, operation_type, work_center,
                machine_number_planned, quantity_ordered, notes, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
        """
        
        for std_op in standard_ops:
            execute_query(
                insert_query,
                (work_order_id, 1, std_op['sequence_number'], std_op['operation_code'],
                 std_op['operation_description'], std_op['operation_type'],
                 std_op['work_center'], std_op['machine_number_planned'],
                 wo['quantity_ordered'], std_op['notes'])
            )
        
        flash(f'Loaded {len(standard_ops)} operations from standard routing', 'success')
        return redirect(url_for('work_orders.edit_routing', work_order_id=work_order_id))
        
    except Exception as e:
        flash(f'Error loading standard routing: {str(e)}', 'danger')
        return redirect(url_for('work_orders.edit_routing', work_order_id=work_order_id))
        return jsonify({'error': str(e)}), 500
