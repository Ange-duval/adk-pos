# -*- coding: utf-8 -*-
from collections import defaultdict

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class AdkPosSession(models.Model):
    _name = 'adk.pos.session'
    _description = "Session de caisse"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'opening_date desc, id desc'

    name = fields.Char(string="Référence", required=True, copy=False, readonly=True, default='/')
    config_id = fields.Many2one('adk.pos.config', string="Point de vente", required=True,
                                ondelete='restrict', tracking=True)
    cashier_id = fields.Many2one('res.users', string="Caissier", required=True, tracking=True,
                                 default=lambda self: self.env.user)
    closed_by_id = fields.Many2one('res.users', string="Fermée par", readonly=True, copy=False)
    approved_by_id = fields.Many2one('res.users', string="Écart validé par", readonly=True, copy=False)

    state = fields.Selection([
        ('opening', 'Ouverture en cours'),
        ('opened', 'En cours'),
        ('closing', 'Contrôle à la fermeture'),
        ('closed', 'Fermée & Comptabilisée'),
    ], default='opening', tracking=True, string="Statut", index=True)

    opening_date = fields.Datetime(default=fields.Datetime.now, string="Date d'ouverture")
    closing_date = fields.Datetime(string="Date de fermeture")

    opening_balance = fields.Monetary(string="Fond de caisse (ouverture)", currency_field='currency_id')
    opening_count_details = fields.Text(string="Comptage à l'ouverture")
    closing_balance_expected = fields.Monetary(compute='_compute_closing_balance_expected',
                                               string="Solde théorique", currency_field='currency_id')
    closing_balance_actual = fields.Monetary(string="Solde compté (fermeture)", currency_field='currency_id')
    closing_count_details = fields.Text(string="Comptage à la fermeture")
    closing_note = fields.Text(string="Note de fermeture")
    cash_difference = fields.Monetary(compute='_compute_cash_difference', string="Écart de caisse",
                                      currency_field='currency_id', store=True)

    currency_id = fields.Many2one(related='config_id.currency_id', string="Devise")
    order_ids = fields.One2many('adk.pos.order', 'session_id', string="Commandes")
    payment_ids = fields.One2many('adk.pos.payment', 'session_id', string="Paiements")
    cash_move_ids = fields.One2many('adk.pos.cash.move', 'session_id', string="Mouvements de caisse")
    cash_in_total = fields.Monetary(compute='_compute_cash_moves', currency_field='currency_id')
    cash_out_total = fields.Monetary(compute='_compute_cash_moves', currency_field='currency_id')
    cash_payments_total = fields.Monetary(compute='_compute_totals', string="Encaissements espèces",
                                          currency_field='currency_id')
    tips_total = fields.Monetary(compute='_compute_totals', string="Pourboires",
                                 currency_field='currency_id')
    order_count = fields.Integer(compute='_compute_order_count')
    total_sales = fields.Monetary(compute='_compute_totals', string="Ventes nettes",
                                  currency_field='currency_id')
    total_orders_paid = fields.Integer(compute='_compute_totals', string="Commandes encaissées")

    company_id = fields.Many2one(related='config_id.company_id', store=True)

    def write(self, vals):
        locked = {'closing_balance_actual', 'closing_count_details', 'opening_balance', 'closing_note',
                  'config_id', 'cashier_id'}
        if locked & set(vals) and not self.env.context.get('adk_force'):
            for session in self:
                if session.state == 'closed':
                    raise UserError(_("La session %s est fermée : la somme comptée et le fond de caisse "
                                      "ne sont plus modifiables.") % session.name)
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code('adk.pos.session') or '/'
        return super().create(vals_list)

    @api.depends('order_ids.amount_total', 'order_ids.state', 'order_ids.tip_amount',
                 'payment_ids.amount', 'payment_ids.is_cash')
    def _compute_totals(self):
        for session in self:
            paid_orders = session.order_ids.filtered(lambda o: o.state == 'paid')
            session.total_sales = sum(paid_orders.mapped('amount_total'))
            session.total_orders_paid = len(paid_orders)
            session.tips_total = sum(paid_orders.mapped('tip_amount'))
            session.cash_payments_total = sum(session.payment_ids.filtered('is_cash').mapped('amount'))

    def _compute_order_count(self):
        for session in self:
            session.order_count = len(session.order_ids)

    @api.depends('cash_move_ids.amount', 'cash_move_ids.move_type')
    def _compute_cash_moves(self):
        for session in self:
            session.cash_in_total = sum(session.cash_move_ids.filtered(
                lambda m: m.move_type == 'in').mapped('amount'))
            session.cash_out_total = sum(session.cash_move_ids.filtered(
                lambda m: m.move_type == 'out').mapped('amount'))

    @api.depends('opening_balance', 'cash_payments_total', 'cash_in_total', 'cash_out_total')
    def _compute_closing_balance_expected(self):
        for session in self:
            session.closing_balance_expected = (
                session.opening_balance + session.cash_payments_total
                + session.cash_in_total - session.cash_out_total)

    @api.depends('closing_balance_actual', 'opening_balance', 'cash_payments_total',
                 'cash_in_total', 'cash_out_total', 'state')
    def _compute_cash_difference(self):
        for session in self:
            if session.state in ('closing', 'closed'):
                expected = (session.opening_balance + session.cash_payments_total
                            + session.cash_in_total - session.cash_out_total)
                session.cash_difference = session.closing_balance_actual - expected
            else:
                session.cash_difference = 0.0

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    def action_open_session(self):
        for session in self:
            if session.state != 'opening':
                raise UserError(_("Cette session ne peut pas être ouverte depuis son état actuel."))
            if self.search_count([('config_id', '=', session.config_id.id), ('state', '=', 'opened'),
                                  ('id', '!=', session.id)]):
                raise UserError(_("Une session est déjà ouverte sur ce point de vente."))
            session.write({'state': 'opened', 'opening_date': fields.Datetime.now()})

    def _check_can_close(self):
        self.ensure_one()
        pending = self.order_ids.filtered(lambda o: o.state not in ('paid', 'cancelled'))
        # Les commandes vides (brouillons oubliés) sont annulées automatiquement
        empty_pending = pending.filtered(lambda o: not o.order_line_ids)
        if empty_pending:
            empty_pending.write({'state': 'cancelled', 'cancel_reason': _("Commande vide (fermeture de caisse)")})
            pending = pending - empty_pending
        if pending:
            raise UserError(_(
                "Impossible de fermer la session : %(count)d commande(s) contiennent des articles "
                "mais ne sont pas encore réglées ou annulées (%(names)s).",
                count=len(pending), names=", ".join(pending.mapped('name'))))

    def action_close_session(self):
        for session in self:
            if session.state not in ('opened', 'closing'):
                raise UserError(_("Cette session est déjà fermée."))
            session._check_can_close()
            session.write({
                'state': 'closed', 'closing_date': fields.Datetime.now(),
                'closed_by_id': session.closed_by_id.id or self.env.user.id,
            })

    def action_view_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Commandes de la session"),
            'res_model': 'adk.pos.order', 'view_mode': 'list,form,kanban',
            'domain': [('session_id', '=', self.id)],
        }

    def action_view_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Paiements de la session"),
            'res_model': 'adk.pos.payment', 'view_mode': 'list,form',
            'domain': [('session_id', '=', self.id)],
        }

    def action_view_cash_moves(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Mouvements de caisse"),
            'res_model': 'adk.pos.cash.move', 'view_mode': 'list,form',
            'domain': [('session_id', '=', self.id)],
            'context': {'default_session_id': self.id},
        }

    def action_print_z_report(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': '/adk_pos/z_report/%s' % self.id, 'target': 'new'}

    # ------------------------------------------------------------------
    # API du terminal
    # ------------------------------------------------------------------
    @api.model
    def _employee(self, employee_id, role):
        return self.env['adk.pos.order']._terminal_employee(employee_id, role)

    @api.model
    def get_open_session_for_user(self):
        """Compatibilité : session ouverte pertinente pour l'utilisateur courant."""
        user = self.env.user
        Config = self.env['adk.pos.config']
        configs = Config.search([
            '|', '|', ('server_ids', 'in', user.id), ('cashier_ids', 'in', user.id),
            ('manager_id', '=', user.id)])
        if not configs:
            configs = Config.search([])
        session = self.search([('config_id', 'in', configs.ids), ('state', '=', 'opened')],
                              limit=1, order='opening_date desc')
        config = session.config_id if session else configs[:1]
        return {
            'session_id': session.id if session else False,
            'session_name': session.name if session else False,
            'config_id': config.id if config else False,
            'config_name': config.name if config else False,
            'use_tables': config.use_tables if config else True,
            'pos_type': config.pos_type if config else False,
            'is_cashier': self.env.user.has_group('adk_pos.group_adk_pos_cashier'),
            'available_config_ids': configs.ids,
        }

    @api.model
    def get_opened_session(self, config_id):
        session = self.search([('config_id', '=', config_id), ('state', '=', 'opened')], limit=1)
        return {'id': session.id, 'name': session.name} if session else False

    @api.model
    def quick_open_session(self, config_id, opening_balance, cashier_id=False, count_details=''):
        """Ouverture d'une session depuis le terminal (réservée aux caissiers et managers)."""
        employee = self._employee(cashier_id or self.env.user.id, 'cashier')
        existing = self.search([('config_id', '=', config_id), ('state', '=', 'opened')], limit=1)
        if existing:
            return existing.id
        session = self.sudo().create({
            'config_id': config_id, 'cashier_id': employee.id,
            'opening_balance': max(float(opening_balance or 0.0), 0.0),
            'opening_count_details': count_details or False,
        })
        session.action_open_session()
        return session.id

    @api.model
    def add_cash_move(self, session_id, move_type, amount, reason, user_id):
        self._employee(user_id, 'cashier')
        if move_type not in ('in', 'out'):
            raise UserError(_("Type de mouvement invalide."))
        return self.env['adk.pos.cash.move'].sudo().create({
            'session_id': session_id, 'move_type': move_type,
            'amount': float(amount or 0.0), 'reason': reason, 'user_id': user_id,
        }).id

    @api.model
    def get_closing_info(self, session_id, employee_id):
        """Récapitulatif affiché avant la fermeture de caisse."""
        employee = self._employee(employee_id, 'cashier')
        session = self.browse(session_id).exists()
        if not session:
            raise UserError(_("Session introuvable."))
        blind = session.config_id.blind_closing and employee._adk_pos_role() != 'manager'
        pending = session.order_ids.filtered(
            lambda o: o.state not in ('paid', 'cancelled') and o.order_line_ids)
        by_method = defaultdict(float)
        for pay in session.payment_ids:
            by_method[pay.payment_method_id.name] += pay.amount
        return {
            'name': session.name,
            'opening_balance': session.opening_balance,
            'blind': blind,
            'expected': False if blind else session.closing_balance_expected,
            'cash_payments': False if blind else session.cash_payments_total,
            'cash_in': False if blind else session.cash_in_total,
            'cash_out': False if blind else session.cash_out_total,
            'total_sales': session.total_sales,
            'orders_paid': session.total_orders_paid,
            'tips_total': session.tips_total,
            'payments': [{'name': k, 'amount': v} for k, v in by_method.items()],
            'pending': [{'id': o.id, 'name': o.name, 'table': o.table_id.name or '',
                         'total': o.amount_total, 'state': o.state} for o in pending],
        }

    @api.model
    def close_session_with_balance(self, session_id, closing_balance_actual, count_details='',
                                   employee_id=False, approver_id=False, approver_pin=False, note=''):
        employee = self._employee(employee_id or self.env.user.id, 'cashier')
        session = self.sudo().browse(session_id).exists()
        if not session:
            raise UserError(_("Session introuvable."))
        if session.state not in ('opened', 'closing'):
            raise UserError(_("Cette session est déjà fermée."))
        session._check_can_close()
        actual = float(closing_balance_actual or 0.0)
        difference = actual - session.closing_balance_expected
        tolerance = session.config_id.max_cash_difference
        approver = False
        if tolerance and abs(difference) > tolerance + 1e-6 and employee._adk_pos_role() != 'manager':
            res = self.env['res.users'].adk_pos_approve(approver_id, approver_pin) \
                if (approver_id and approver_pin) else {}
            if not res.get('ok'):
                raise AccessError(_(
                    "L'écart de caisse (%s) dépasse la tolérance autorisée : la validation d'un "
                    "manager (code PIN) est nécessaire.") % difference)
            approver = approver_id
        session.write({
            'closing_balance_actual': actual,
            'closing_count_details': count_details or False,
            'closing_note': note or False,
            'state': 'closed', 'closing_date': fields.Datetime.now(),
            'closed_by_id': employee.id,
            'approved_by_id': approver or False,
        })
        if difference:
            session.message_post(body=_("Fermeture par %s avec un écart de caisse de %s.") % (
                employee.name, difference))
        return {'id': session.id, 'difference': difference}

    # ------------------------------------------------------------------
    # Rapport X / Z
    # ------------------------------------------------------------------
    def _fmt_money(self, value):
        self.ensure_one()
        cur = self.currency_id
        number = '{:,.{d}f}'.format(value or 0.0, d=cur.decimal_places).replace(',', ' ')
        return "%s %s" % (number, cur.symbol) if cur.position == 'after' else "%s %s" % (cur.symbol, number)

    def get_z_report_data(self):
        """Données du rapport X (session en cours) ou Z (session fermée)."""
        self.ensure_one()
        paid_orders = self.order_ids.filtered(lambda o: o.state == 'paid')
        lines = paid_orders.mapped('order_line_ids')

        categories = {}
        for line in lines:
            categ = line.product_id.categ_id
            key = categ.id if categ else 0
            label = (categ.complete_name or categ.name or '').replace('All / ', '').strip() if categ else ''
            if not label or label == 'All':
                label = _("Sans catégorie")
            cat = categories.setdefault(key, {'name': label, 'qty': 0.0, 'total': 0.0, 'lines': {}})
            cat['qty'] += line.qty
            cat['total'] += line.price_total
            prod = cat['lines'].setdefault(line.product_id.id, {
                'name': line.product_id.display_name,
                'uom': line.product_id.uom_id.name or _("Unité(s)"), 'qty': 0.0, 'total': 0.0})
            prod['qty'] += line.qty
            prod['total'] += line.price_total
        sales_by_category = [{
            'name': c['name'], 'qty': c['qty'], 'total': c['total'],
            'lines': sorted(c['lines'].values(), key=lambda l: -l['total']),
        } for c in sorted(categories.values(), key=lambda c: c['name'])]
        total_qty = sum(c['qty'] for c in sales_by_category)
        total_amount = sum(c['total'] for c in sales_by_category)

        taxes = {}
        for line in lines:
            if line.tax_ids:
                for tax in line.tax_ids:
                    t = taxes.setdefault(tax.id, {'name': tax.name, 'base': 0.0, 'amount': 0.0})
                    t['base'] += line.price_subtotal
                    t['amount'] += (line.price_total - line.price_subtotal)
            else:
                t = taxes.setdefault('none', {'name': _("Pas de taxe"), 'base': 0.0, 'amount': 0.0})
                t['base'] += line.price_subtotal
        tax_lines = list(taxes.values())

        pay_by_method = {}
        for pay in self.payment_ids:
            m = pay.payment_method_id
            p = pay_by_method.setdefault(m.id, {
                'name': m.name, 'is_cash': m.is_cash, 'is_credit': m.is_credit, 'amount': 0.0, 'count': 0})
            p['amount'] += pay.amount
            p['count'] += 1
        payment_lines = sorted(pay_by_method.values(), key=lambda p: -p['amount'])

        discounted = lines.filtered(lambda l: l.discount)
        discount_amount = sum(discounted.mapped('discount_amount'))

        counted_known = self.state in ('closing', 'closed')
        session_control = []
        for p in payment_lines:
            if p['is_cash']:
                expected = (self.opening_balance + p['amount'] + self.cash_in_total - self.cash_out_total)
                counted = self.closing_balance_actual if counted_known else expected
            else:
                expected = counted = p['amount']
            session_control.append({
                'name': p['name'], 'expected': expected, 'counted': counted,
                'difference': counted - expected,
            })

        refunds = paid_orders.filtered('refund_of_id')
        by_cashier = defaultdict(lambda: {'count': 0, 'total': 0.0})
        for order in paid_orders.filtered(lambda o: not o.refund_of_id):
            by_cashier[order.cashier_id.name or '—']['count'] += 1
            by_cashier[order.cashier_id.name or '—']['total'] += order.amount_total
        type_labels = dict(self.env['adk.pos.order']._fields['order_type'].selection)
        by_type = defaultdict(lambda: {'count': 0, 'total': 0.0})
        for order in paid_orders.filtered(lambda o: not o.refund_of_id):
            by_type[type_labels.get(order.order_type, '')]['count'] += 1
            by_type[type_labels.get(order.order_type, '')]['total'] += order.amount_total

        # --- Détail commande par commande ---
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        state_labels = {'not_paid': _("Non payée"), 'partial': _("Partielle"), 'paid': _("Payée"),
                        'in_payment': _("Payée"), 'reversed': _("Annulée")}
        order_rows = []
        for order in paid_orders.sorted(lambda o: o.date_paid or o.date_order):
            when = order.date_paid or order.date_order
            move = order.invoice_id or order.credit_note_id
            order_rows.append({
                'name': order.name,
                'time': pytz.utc.localize(when).astimezone(tz).strftime('%H:%M') if when else '',
                'table': order.table_id.name or type_labels.get(order.order_type, ''),
                'server': order.server_id.name or '',
                'partner': order.partner_id.name or '',
                'methods': ', '.join(order.payment_ids.mapped('payment_method_id.name')),
                'total': order.amount_total,
                'invoice': move.name if move else '',
                'invoice_state': state_labels.get(move.payment_state, '') if move else '',
                'is_refund': bool(order.refund_of_id),
            })
        by_server = defaultdict(lambda: {'count': 0, 'total': 0.0})
        for order in paid_orders.filtered(lambda o: not o.refund_of_id):
            by_server[order.server_id.name or '—']['count'] += 1
            by_server[order.server_id.name or '—']['total'] += order.amount_total
        by_hour = defaultdict(lambda: {'count': 0, 'total': 0.0})
        for order in paid_orders.filtered(lambda o: not o.refund_of_id):
            when = order.date_paid or order.date_order
            if when:
                hour = pytz.utc.localize(when).astimezone(tz).strftime('%Hh')
                by_hour[hour]['count'] += 1
                by_hour[hour]['total'] += order.amount_total
        credit_total = sum(p['amount'] for p in payment_lines if p.get('is_credit'))
        top_products = sorted(
            [pr for c in sales_by_category for pr in c['lines']], key=lambda l: -l['total'])[:5]

        return {
            'company': self.company_id,
            'session': self,
            'order_rows': order_rows,
            'by_server': [{'name': k, **v} for k, v in sorted(by_server.items(), key=lambda kv: -kv[1]['total'])],
            'by_hour': [{'name': k, **v} for k, v in sorted(by_hour.items())],
            'credit_total': credit_total,
            'top_products': top_products,
            'fmt': self._fmt_money,
            'is_closed': self.state == 'closed',
            'report_letter': 'Z' if self.state == 'closed' else 'X',
            'sales_by_category': sales_by_category,
            'total_qty': total_qty,
            'total_amount': total_amount,
            'tax_lines': tax_lines,
            'payment_lines': payment_lines,
            'payments_total': sum(p['amount'] for p in payment_lines),
            'discount_count': len(discounted),
            'discount_amount': discount_amount,
            'session_control': session_control,
            'transaction_count': len(paid_orders),
            'refund_count': len(refunds),
            'refund_amount': -sum(refunds.mapped('amount_total')),
            'tips_total': self.tips_total,
            'cash_in_total': self.cash_in_total,
            'cash_out_total': self.cash_out_total,
            'cash_moves': [{'type': m.move_type, 'reason': m.reason, 'amount': m.amount,
                            'user': m.user_id.name} for m in self.cash_move_ids],
            'cashiers': [{'name': k, **v} for k, v in by_cashier.items()],
            'order_types': [{'name': k, **v} for k, v in by_type.items()],
            'opening_count_details': self.opening_count_details,
            'closing_count_details': self.closing_count_details,
        }
