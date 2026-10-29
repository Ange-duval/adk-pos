# -*- coding: utf-8 -*-
import time
from collections import defaultdict
from urllib.parse import quote

from odoo import http, fields
from odoo.http import request

COURSE_LABELS = [
    ('drink', "Boissons"), ('starter', "Entrées"), ('main', "Plats"),
    ('dessert', "Desserts"), ('other', "Divers"),
]

# Limitation simple des commandes publiques (QR / Kiosque) : par IP et par minute
_KIOSK_HITS = defaultdict(list)
KIOSK_MAX_PER_MINUTE = 6
KIOSK_MAX_LINES = 40
KIOSK_MAX_QTY = 50


class AdkPosController(http.Controller):

    @http.route('/adk_pos/receipt/<int:order_id>', type='http', auth='user')
    def print_receipt(self, order_id, **kwargs):
        order = request.env['adk.pos.order'].browse(order_id)
        if not order.exists():
            return request.not_found()
        return request.render('adk_pos.report_receipt_document', {
            'order': order, 'is_bill': False, 'basic': bool(kwargs.get('basic'))})

    @http.route('/adk_pos/bill/<int:order_id>', type='http', auth='user')
    def print_bill(self, order_id, **kwargs):
        """Addition (pré-ticket) à présenter au client avant l'encaissement."""
        order = request.env['adk.pos.order'].browse(order_id)
        if not order.exists():
            return request.not_found()
        return request.render('adk_pos.report_receipt_document', {'order': order, 'is_bill': True, 'basic': False})

    @http.route('/adk_pos/kitchen_ticket/<int:order_id>/<int:batch>', type='http', auth='user')
    def print_kitchen_ticket(self, order_id, batch, **kwargs):
        order = request.env['adk.pos.order'].browse(order_id)
        if not order.exists():
            return request.not_found()
        items = order.kitchen_item_ids.filtered(lambda i: i.batch_no == batch and i.state != 'cancelled')
        courses = []
        for code, label in COURSE_LABELS:
            chosen = items.filtered(lambda i, c=code: i.course == c)
            if chosen:
                courses.append({'label': label, 'items': chosen})
        return request.render('adk_pos.report_kitchen_ticket',
                              {'order': order, 'batch': batch, 'courses': courses})

    @http.route('/adk_pos/z_report/<int:session_id>', type='http', auth='user')
    def print_z_report(self, session_id, **kwargs):
        session = request.env['adk.pos.session'].browse(session_id)
        if not session.exists():
            return request.not_found()
        return request.render('adk_pos.report_z_document', {'z': session.get_z_report_data()})

    @http.route('/adk_pos/table_qrs/<int:config_id>', type='http', auth='user')
    def print_table_qrs(self, config_id, **kwargs):
        config = request.env['adk.pos.config'].browse(config_id)
        if not config.exists():
            return request.not_found()
        tables = request.env['adk.pos.table'].search([('config_id', '=', config.id)])
        base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return request.render('adk_pos.report_table_qrs', {
            'config': config, 'tables': tables, 'base_url': base_url,
            'url_quote': lambda s: quote(s, safe=''),
        })

    # ------------------------------------------------------------------
    # Commande autonome (QR / Kiosque)
    # ------------------------------------------------------------------
    @http.route('/adk_pos/kiosk/<int:config_id>', type='http', auth='public', website=False)
    def kiosk_page(self, config_id, table_id=None, **kwargs):
        config = request.env['adk.pos.config'].sudo().browse(config_id)
        if not config.exists() or not config.kiosk_enabled:
            return request.render('adk_pos.kiosk_unavailable', {})

        domain = [
            ('sale_ok', '=', True),
            '|', ('adk_pos_config_ids', '=', False), ('adk_pos_config_ids', 'in', [config.id]),
        ]
        if config.limit_categories and config.category_ids:
            domain.append(('categ_id', 'child_of', config.category_ids.ids))
        products = request.env['product.product'].sudo().search(domain)
        categories = products.mapped('categ_id')
        table = None
        if table_id and str(table_id).isdigit():
            table = request.env['adk.pos.table'].sudo().browse(int(table_id))
            if not table.exists() or table.config_id != config:
                table = None

        return request.render('adk_pos.kiosk_page', {
            'config': config, 'table': table, 'categories': categories, 'products': products,
        })

    @http.route('/adk_pos/kiosk/<int:config_id>/submit', type='json', auth='public')
    def kiosk_submit(self, config_id, lines=None, table_id=None, customer_note=None, **kwargs):
        config = request.env['adk.pos.config'].sudo().browse(config_id)
        if not config.exists() or not config.kiosk_enabled:
            return {'error': "Commande autonome non disponible."}
        if config.kiosk_mode == 'qr_menu':
            return {'error': "Ce menu est en consultation seulement. Merci de commander auprès d'un serveur."}

        # Anti-abus : nombre de commandes par minute et par adresse IP
        ip = request.httprequest.remote_addr or '?'
        now = time.time()
        _KIOSK_HITS[ip] = [t for t in _KIOSK_HITS[ip] if now - t < 60]
        if len(_KIOSK_HITS[ip]) >= KIOSK_MAX_PER_MINUTE:
            return {'error': "Trop de commandes en peu de temps. Merci de patienter une minute."}
        _KIOSK_HITS[ip].append(now)

        session = request.env['adk.pos.session'].sudo().search(
            [('config_id', '=', config.id), ('state', '=', 'opened')], limit=1)
        if not session:
            return {'error': "Le point de vente n'est pas encore ouvert. Merci de réessayer plus tard."}

        if not lines or not isinstance(lines, list):
            return {'error': "Votre panier est vide."}
        if len(lines) > KIOSK_MAX_LINES:
            return {'error': "Panier trop volumineux."}

        Product = request.env['product.product'].sudo()
        clean_lines = []
        for line in lines:
            try:
                product = Product.browse(int(line['product_id'])).exists()
                qty = float(line.get('qty', 1))
            except (KeyError, TypeError, ValueError):
                return {'error': "Panier invalide."}
            if not product or not product.sale_ok or qty <= 0 or qty > KIOSK_MAX_QTY:
                return {'error': "Article ou quantité invalide."}
            if product.adk_pos_config_ids and config not in product.adk_pos_config_ids:
                return {'error': "« %s » n'est pas disponible ici." % product.name}
            clean_lines.append((product, qty))

        table = False
        if table_id and str(table_id).isdigit() and config.kiosk_service != 'counter':
            table_rec = request.env['adk.pos.table'].sudo().browse(int(table_id)).exists()
            if table_rec and table_rec.config_id == config:
                table = table_rec.id

        note = (customer_note or '').strip()[:300]
        order = request.env['adk.pos.order'].sudo().create({
            'config_id': config.id, 'session_id': session.id, 'table_id': table,
            'server_id': config.manager_id.id or session.cashier_id.id,
            'source': 'kiosk', 'order_type': 'takeaway' if config.kiosk_service == 'counter' else 'dine_in',
            'note': ("%s [Commande client QR]" % note) if note else "[Commande client QR]",
            'order_line_ids': [(0, 0, {'product_id': p.id, 'qty': q}) for p, q in clean_lines],
        })
        if config.kiosk_auto_send:
            order._send_to_kitchen()
        return {'success': True, 'order_name': order.name}

    # ------------------------------------------------------------------
    # Afficheur client (second écran / tablette tournée vers le client)
    # ------------------------------------------------------------------
    @http.route('/adk_pos/customer_display/<int:config_id>', type='http', auth='user')
    def customer_display(self, config_id, **kwargs):
        config = request.env['adk.pos.config'].browse(config_id)
        if not config.exists() or not config.customer_display:
            return request.render('adk_pos.kiosk_unavailable', {})
        return request.render('adk_pos.customer_display_page', {'config': config})

    @http.route('/adk_pos/customer_display/<int:config_id>/data', type='json', auth='user')
    def customer_display_data(self, config_id, **kwargs):
        config = request.env['adk.pos.config'].browse(config_id)
        if not config.exists() or not config.customer_display:
            return {}
        order = request.env['adk.pos.order'].search([
            ('config_id', '=', config.id), ('state', 'in', ('draft', 'sent', 'billing'))],
            order='write_date desc', limit=1)
        paid = request.env['adk.pos.order'].search([
            ('config_id', '=', config.id), ('state', '=', 'paid')], order='date_paid desc', limit=1)
        show_paid = bool(paid and (not order or paid.date_paid and paid.date_paid > order.write_date)
                         and (fields.Datetime.now() - paid.date_paid).total_seconds() < 12)
        cur = config.currency_id

        def money(v):
            number = '{:,.{d}f}'.format(v or 0.0, d=cur.decimal_places).replace(',', ' ')
            return "%s %s" % (number, cur.symbol) if cur.position == 'after' else "%s %s" % (cur.symbol, number)
        if show_paid:
            return {'state': 'paid', 'total': money(paid.amount_total + paid.tip_amount),
                    'change': money(paid.amount_change) if paid.amount_change else ''}
        if not order or not order.order_line_ids:
            return {'state': 'idle', 'name': config.name, 'text': config.kiosk_welcome_text or ''}
        return {'state': 'order', 'name': order.name, 'total': money(order.amount_total),
                'lines': [{'name': l.name, 'qty': l.qty, 'total': money(l.price_total)} for l in order.order_line_ids]}
