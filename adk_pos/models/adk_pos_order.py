# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

ROLE_LEVEL = {'server': 1, 'cashier': 2, 'manager': 3}
OPEN_STATES = ('draft', 'sent', 'billing')


class AdkPosOrder(models.Model):
    _name = 'adk.pos.order'
    _description = "Commande Point de Vente"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    name = fields.Char(string="N° Commande", required=True, copy=False, readonly=True, default='/')
    config_id = fields.Many2one('adk.pos.config', string="Point de vente", required=True,
                                ondelete='restrict', tracking=True)
    session_id = fields.Many2one('adk.pos.session', string="Session", required=True,
                                 ondelete='restrict', tracking=True,
                                 domain="[('config_id', '=', config_id), ('state', '=', 'opened')]")
    table_id = fields.Many2one('adk.pos.table', string="Table",
                               domain="[('config_id', '=', config_id)]", tracking=True)

    server_id = fields.Many2one('res.users', string="Serveur / Pris par", tracking=True,
                                default=lambda self: self.env.user)
    cashier_id = fields.Many2one('res.users', string="Encaissé par", tracking=True)
    partner_id = fields.Many2one('res.partner', string="Client")
    customer_phone = fields.Char(string="Téléphone du client")
    delivery_address = fields.Char(string="Adresse de livraison")

    order_type = fields.Selection([
        ('dine_in', 'Sur place'),
        ('takeaway', 'À emporter'),
        ('delivery', 'Livraison'),
    ], string="Type de commande", default='dine_in', required=True, tracking=True)
    source = fields.Selection([
        ('pos', 'Terminal'), ('kiosk', 'QR / Kiosque'),
    ], string="Origine", default='pos', copy=False)

    order_line_ids = fields.One2many('adk.pos.order.line', 'order_id', string="Lignes de commande")
    payment_ids = fields.One2many('adk.pos.payment', 'order_id', string="Paiements")
    kitchen_item_ids = fields.One2many('adk.pos.kitchen.item', 'order_id', string="Articles en préparation")
    send_count = fields.Integer(string="Envois en cuisine", default=0, copy=False)

    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('sent', 'Envoyée en préparation'),
        ('billing', 'En facturation'),
        ('paid', 'Payée'),
        ('cancelled', 'Annulée'),
    ], default='draft', tracking=True, string="Statut", index=True)

    # Paiement (calculé à partir des paiements enregistrés)
    payment_method_id = fields.Many2one('adk.pos.payment.method', string="Mode de paiement",
                                        compute='_compute_payment_totals', store=True)
    amount_paid = fields.Monetary(string="Montant payé", compute='_compute_payment_totals',
                                  store=True, currency_field='currency_id')
    amount_due = fields.Monetary(string="Reste à payer", compute='_compute_payment_totals',
                                 store=True, currency_field='currency_id')
    amount_change = fields.Monetary(string="Monnaie rendue", compute='_compute_payment_totals',
                                    store=True, currency_field='currency_id')
    payment_reference = fields.Char(string="Référence / Pièce justificative",
                                    compute='_compute_payment_totals', store=True)
    tip_amount = fields.Monetary(string="Pourboire", currency_field='currency_id', copy=False)

    guests = fields.Integer(string="Nombre de couverts", default=1)
    note = fields.Text(string="Note / Instructions")
    cancel_reason = fields.Char(string="Motif d'annulation", copy=False)

    refund_of_id = fields.Many2one('adk.pos.order', string="Remboursement de", readonly=True, copy=False)
    refund_ids = fields.One2many('adk.pos.order', 'refund_of_id', string="Remboursements")
    refund_count = fields.Integer(compute='_compute_refund_count')
    is_refund = fields.Boolean(compute='_compute_is_refund', store=True)
    reservation_id = fields.Many2one('adk.pos.reservation', string="Réservation", copy=False,
                                     ondelete='set null')

    currency_id = fields.Many2one(related='config_id.currency_id', string="Devise")
    amount_untaxed = fields.Monetary(compute='_compute_amounts', store=True,
                                     string="Total HT", currency_field='currency_id')
    amount_tax = fields.Monetary(compute='_compute_amounts', store=True,
                                 string="Taxes", currency_field='currency_id')
    amount_total = fields.Monetary(compute='_compute_amounts', store=True,
                                   string="Total TTC", currency_field='currency_id')
    discount_total = fields.Monetary(compute='_compute_amounts', store=True,
                                     string="Remises", currency_field='currency_id')
    margin = fields.Monetary(compute='_compute_amounts', store=True,
                             string="Marge", currency_field='currency_id')
    margin_percent = fields.Float(compute='_compute_amounts', store=True, string="Marge (%)")

    date_order = fields.Datetime(default=fields.Datetime.now, string="Date de commande")
    date_paid = fields.Datetime(string="Date d'encaissement")

    company_id = fields.Many2one(related='config_id.company_id', store=True)

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends('refund_of_id')
    def _compute_is_refund(self):
        for order in self:
            order.is_refund = bool(order.refund_of_id)

    def _compute_refund_count(self):
        for order in self:
            order.refund_count = len(order.refund_ids)

    @api.depends('order_line_ids.price_subtotal', 'order_line_ids.price_tax',
                 'order_line_ids.price_total', 'order_line_ids.margin',
                 'order_line_ids.discount_amount')
    def _compute_amounts(self):
        for order in self:
            lines = order.order_line_ids
            order.amount_untaxed = sum(lines.mapped('price_subtotal'))
            order.amount_tax = sum(lines.mapped('price_tax'))
            order.amount_total = sum(lines.mapped('price_total'))
            order.discount_total = sum(lines.mapped('discount_amount'))
            order.margin = sum(lines.mapped('margin'))
            order.margin_percent = (order.margin / order.amount_untaxed) if order.amount_untaxed else 0.0

    @api.depends('payment_ids.amount', 'payment_ids.change_given', 'payment_ids.reference',
                 'payment_ids.payment_method_id', 'amount_total', 'tip_amount')
    def _compute_payment_totals(self):
        for order in self:
            payments = order.payment_ids
            paid = sum(payments.mapped('amount'))
            due = order.amount_total + (order.tip_amount or 0.0) - paid
            order.amount_paid = paid
            order.amount_due = 0.0 if order.currency_id.is_zero(due) else due
            order.amount_change = sum(payments.mapped('change_given'))
            main = payments.sorted(lambda p: -abs(p.amount))[:1]
            order.payment_method_id = main.payment_method_id.id if main else False
            order.payment_reference = ", ".join(p.reference for p in payments if p.reference) or False

    @api.constrains('config_id', 'session_id')
    def _check_session_config(self):
        for order in self:
            if order.session_id.config_id != order.config_id:
                raise ValidationError(_("La session choisie n'appartient pas à ce point de vente."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == '/':
                name = self.env['ir.sequence'].next_by_code('adk.pos.order') or '/'
                cfg = self.env['adk.pos.config'].browse(vals.get('config_id')).exists()
                if cfg.order_prefix and name.startswith('CMD'):
                    name = cfg.order_prefix.strip() + name[3:]
                vals['name'] = name
        return super().create(vals_list)

    def _fmt(self, value):
        """Montant formaté avec la devise du point de vente (pour les tickets)."""
        self.ensure_one()
        cur = self.currency_id
        number = '{:,.{d}f}'.format(value or 0.0, d=cur.decimal_places).replace(',', ' ')
        return "%s %s" % (number, cur.symbol) if cur.position == 'after' else "%s %s" % (cur.symbol, number)

    # ------------------------------------------------------------------
    # Boutons du back-office
    # ------------------------------------------------------------------
    def action_view_refunds(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Remboursements"),
            'res_model': 'adk.pos.order', 'view_mode': 'list,form',
            'domain': [('refund_of_id', '=', self.id)],
        }

    def action_send_to_kitchen(self):
        for order in self:
            order._send_to_kitchen()
        return True

    def action_go_billing(self):
        for order in self:
            if order.state not in ('draft', 'sent'):
                raise UserError(_("Cette commande ne peut pas passer en facturation depuis son statut actuel."))
            if not order.order_line_ids:
                raise UserError(_("Impossible de facturer une commande vide."))
            order.state = 'billing'
        return True

    def action_pay(self):
        for order in self:
            if order.state in ('paid', 'cancelled'):
                raise UserError(_("Cette commande est déjà clôturée."))
            if not order.order_line_ids:
                raise UserError(_("Impossible d'encaisser une commande vide."))
            if not order.currency_id.is_zero(order.amount_due):
                raise UserError(_(
                    "Il reste %s à payer : l'encaissement se fait depuis le terminal "
                    "(Point de Vente) ou via l'onglet « Paiements ».") % order.amount_due)
            order._finalize_payment()
        return True

    def action_cancel(self):
        for order in self:
            order._cancel(reason=_("Annulée depuis le back-office"))
        return True

    def action_open_refund_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Rembourser"),
            'res_model': 'adk.pos.refund.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_order_id': self.id},
        }

    # ------------------------------------------------------------------
    # Logique métier interne
    # ------------------------------------------------------------------
    def _send_to_kitchen(self):
        """Crée un ticket de préparation pour les quantités pas encore envoyées."""
        self.ensure_one()
        if self.state in ('paid', 'cancelled'):
            raise UserError(_("Cette commande est clôturée."))
        Station = self.env['adk.pos.station'].sudo()
        item_vals = []
        batch = self.send_count + 1
        for line in self.order_line_ids:
            delta = line.qty - line.qty_sent
            if delta <= 0:
                continue
            station = Station._get_for_product(self.config_id, line.product_id)
            item_vals.append({
                'order_id': self.id, 'line_id': line.id, 'product_id': line.product_id.id,
                'name': line.product_id.display_name, 'qty': delta, 'note': line.note or False,
                'course': line.course or 'other', 'station_id': station.id or False,
                'batch_no': batch,
            })
            line.qty_sent = line.qty
        if not item_vals:
            if self.state == 'draft':
                raise UserError(_("Impossible d'envoyer une commande vide en préparation."))
            raise UserError(_("Aucun nouvel article à envoyer en préparation."))
        self.env['adk.pos.kitchen.item'].sudo().create(item_vals)
        vals = {'send_count': batch}
        if self.state == 'draft':
            vals['state'] = 'sent'
        self.write(vals)
        return batch

    def _cancel(self, reason='', employee=None):
        self.ensure_one()
        if self.state == 'paid':
            raise UserError(_("Une commande payée ne peut pas être annulée : utilisez un remboursement."))
        if self.state == 'cancelled':
            return
        self.kitchen_item_ids.sudo().filtered(
            lambda i: i.state in ('new', 'preparing')).write({'state': 'cancelled'})
        self.write({'state': 'cancelled', 'cancel_reason': reason or False})
        who = employee.name if employee else self.env.user.name
        self.message_post(body=_("Commande annulée par %s. Motif : %s") % (who, reason or _("non précisé")))

    def _finalize_payment(self, employee=None):
        for order in self:
            if order.state in ('paid', 'cancelled'):
                continue
            if not order.order_line_ids or not order.currency_id.is_zero(order.amount_due):
                continue
            order.write({
                'state': 'paid',
                'date_paid': fields.Datetime.now(),
                'cashier_id': (employee.id if employee else order.cashier_id.id or self.env.user.id),
            })
            if order.reservation_id and order.reservation_id.state == 'seated':
                order.reservation_id.sudo().write({'state': 'done'})

    def _create_refund(self, line_qtys, reason, method, employee, session):
        """Crée un remboursement (total ou partiel) dans la session ouverte donnée."""
        self.ensure_one()
        if self.state != 'paid' or self.refund_of_id:
            raise UserError(_("Seule une commande payée (et non un remboursement) peut être remboursée."))
        if session.state != 'opened' or session.config_id != self.config_id:
            raise UserError(_("Ouvrez d'abord une session de caisse sur ce point de vente."))
        lines_vals = []
        for line in self.order_line_ids:
            qty = float(line_qtys.get(line.id, line_qtys.get(str(line.id), 0)) or 0)
            if qty <= 0:
                continue
            available = line.qty - line.refunded_qty
            if qty > available + 1e-6:
                raise UserError(_("« %s » : vous ne pouvez rembourser que %s unité(s) au maximum.")
                                % (line.name, available))
            lines_vals.append((0, 0, {
                'product_id': line.product_id.id, 'name': line.name, 'qty': -qty,
                'price_unit': line.price_unit, 'discount': line.discount,
                'tax_ids': [(6, 0, line.tax_ids.ids)], 'refunded_line_id': line.id,
                'course': line.course, 'qty_sent': 0.0,
            }))
        if not lines_vals:
            raise UserError(_("Sélectionnez au moins un article à rembourser."))
        refund = self.sudo().create({
            'config_id': self.config_id.id, 'session_id': session.id,
            'server_id': employee.id, 'cashier_id': employee.id,
            'partner_id': self.partner_id.id, 'order_type': self.order_type,
            'refund_of_id': self.id, 'guests': 0, 'source': 'pos',
            'note': _("Remboursement de %s — %s") % (self.name, reason or _("sans motif")),
            'order_line_ids': lines_vals, 'state': 'billing',
        })
        self.env['adk.pos.payment'].sudo().create({
            'order_id': refund.id, 'payment_method_id': (method or self.payment_method_id).id,
            'amount': refund.amount_total, 'tendered': 0.0,
            'reference': _("Remb. %s") % self.name, 'cashier_id': employee.id,
        })
        refund.write({'state': 'paid', 'date_paid': fields.Datetime.now()})
        refund._generate_invoice()
        self.message_post(body=_("Remboursement %s de %s créé par %s. Motif : %s") % (
            refund.name, refund.amount_total, employee.name, reason or _("non précisé")))
        return refund

    # ------------------------------------------------------------------
    # API du terminal (appelée par l'écran tactile)
    # ------------------------------------------------------------------
    @api.model
    def _terminal_employee(self, employee_id, role='server'):
        employee = self.env['res.users'].sudo().browse(employee_id).exists()
        user_role = employee._adk_pos_role() if employee else False
        if not user_role or ROLE_LEVEL[user_role] < ROLE_LEVEL[role]:
            raise AccessError(_("Ce collaborateur n'a pas les droits nécessaires pour cette action."))
        return employee

    @api.model
    def _require_manager(self, employee, approver_id=False, approver_pin=False):
        """Exige l'accord d'un manager : l'employé lui-même, ou un manager qui saisit son PIN."""
        if employee._adk_pos_role() == 'manager':
            return employee
        if approver_id and approver_pin:
            res = self.env['res.users'].adk_pos_approve(approver_id, approver_pin)
            if res.get('ok'):
                return self.env['res.users'].sudo().browse(approver_id)
        raise AccessError(_("La validation d'un manager (code PIN) est nécessaire pour cette action."))

    @api.model
    def _terminal_order(self, order_id):
        order = self.browse(order_id).exists()
        if not order:
            raise UserError(_("Commande introuvable (elle a peut-être été supprimée)."))
        return order

    def _terminal_dict(self):
        self.ensure_one()
        lines = []
        for line in self.order_line_ids:
            lines.append({
                'id': line.id, 'product_id': line.product_id.id, 'name': line.name,
                'qty': line.qty, 'qty_sent': line.qty_sent, 'price_unit': line.price_unit,
                'discount': line.discount, 'note': line.note or '', 'course': line.course or 'other',
                'price_subtotal': line.price_subtotal, 'price_total': line.price_total,
                'refunded_qty': line.refunded_qty,
                'refundable_qty': max(line.qty - line.refunded_qty, 0.0) if line.qty > 0 else 0.0,
                'taxes': ", ".join(line.tax_ids.mapped('name')),
                'categ': line.product_id.categ_id.name or '',
                'cost': (line.product_id.standard_price or 0.0) * line.qty,
                'margin': line.margin,
            })
        payments = [{
            'id': p.id, 'method_id': p.payment_method_id.id, 'method': p.payment_method_id.name,
            'amount': p.amount, 'tendered': p.tendered, 'change': p.change_given,
            'reference': p.reference or '', 'is_cash': p.is_cash,
        } for p in self.payment_ids]
        return {
            'id': self.id, 'name': self.name, 'state': self.state,
            'config_id': self.config_id.id, 'session_id': self.session_id.id,
            'table_id': [self.table_id.id, self.table_id.name] if self.table_id else False,
            'partner_id': [self.partner_id.id, self.partner_id.name] if self.partner_id else False,
            'customer_phone': self.customer_phone or '', 'delivery_address': self.delivery_address or '',
            'order_type': self.order_type, 'source': self.source,
            'guests': self.guests, 'note': self.note or '',
            'server': self.server_id.name or '', 'is_refund': self.is_refund,
            'refund_of': self.refund_of_id.name if self.refund_of_id else '',
            'amount_untaxed': self.amount_untaxed, 'amount_tax': self.amount_tax,
            'amount_total': self.amount_total, 'amount_paid': self.amount_paid,
            'amount_due': self.amount_due, 'amount_change': self.amount_change,
            'tip_amount': self.tip_amount, 'discount_total': self.discount_total,
            'send_count': self.send_count, 'lines': lines, 'payments': payments,
            'has_unsent': any(l['qty'] > l['qty_sent'] for l in lines),
            'reservation': self.reservation_id.name if self.reservation_id else '',
            'date_order': fields.Datetime.to_string(self.date_order),
        }

    @api.model
    def terminal_get_order(self, order_id):
        return self._terminal_order(order_id)._terminal_dict()

    @api.model
    def terminal_create_order(self, config_id, session_id, employee_id, table_id=False,
                              order_type='dine_in', guests=1, force_new=False):
        employee = self._terminal_employee(employee_id, 'server')
        session = self.env['adk.pos.session'].browse(session_id).exists()
        if not session or session.state != 'opened' or session.config_id.id != config_id:
            raise UserError(_("La session de caisse n'est plus ouverte. Rouvrez la caisse."))
        if table_id:
            table = self.env['adk.pos.table'].browse(table_id).exists()
            if not table or table.config_id.id != config_id:
                raise UserError(_("Table introuvable pour ce point de vente."))
            if not force_new:
                existing = self.sudo().search([
                    ('table_id', '=', table.id), ('state', 'in', OPEN_STATES)], limit=1, order='id')
                if existing:
                    return existing.id
            if table.is_reserved:
                table.sudo().write({'is_reserved': False, 'reservation_note': ''})
        order = self.create({
            'config_id': config_id, 'session_id': session.id, 'table_id': table_id or False,
            'server_id': employee.id, 'order_type': order_type or 'dine_in',
            'guests': max(int(guests or 1), 1),
        })
        return order.id

    @api.model
    def terminal_orders(self, config_id, scope='open', search='', limit=80):
        domain = [('config_id', '=', config_id)]
        if scope == 'open':
            domain += [('state', 'in', OPEN_STATES), ('order_line_ids', '!=', False)]
        elif scope == 'paid':
            since = fields.Datetime.now() - timedelta(days=3)
            domain += [('state', '=', 'paid'), ('date_paid', '>=', since)]
        if search:
            domain += ['|', '|', '|', ('name', 'ilike', search), ('table_id.name', 'ilike', search),
                       ('partner_id.name', 'ilike', search), ('customer_phone', 'ilike', search)]
        orders = self.search(domain, limit=limit, order='date_order desc, id desc')
        now = fields.Datetime.now()
        return [{
            'id': o.id, 'name': o.name, 'state': o.state, 'source': o.source,
            'table': o.table_id.name or '', 'server': o.server_id.name or '',
            'partner': o.partner_id.name or '', 'order_type': o.order_type,
            'amount_total': o.amount_total, 'amount_due': o.amount_due,
            'is_refund': o.is_refund, 'refund_of': o.refund_of_id.name or '',
            'age_min': int((now - o.date_order).total_seconds() // 60),
            'method': o.payment_method_id.name or '',
            'fully_refunded': (o.state == 'paid' and not o.is_refund and bool(o.order_line_ids)
                               and all(l.refunded_qty >= l.qty - 1e-6 for l in o.order_line_ids)),
        } for o in orders]

    @api.model
    def terminal_update(self, order_id, vals):
        order = self._terminal_order(order_id)
        allowed = {'guests', 'note', 'partner_id', 'order_type', 'customer_phone', 'delivery_address'}
        if order.state in ('paid', 'cancelled'):
            if order.state == 'paid' and set(vals or {}) <= {'partner_id', 'customer_phone'}:
                allowed = {'partner_id', 'customer_phone'}  # seul le client reste modifiable
            else:
                raise UserError(_("Cette commande est validée : elle n'est plus modifiable."))
        clean = {k: v for k, v in (vals or {}).items() if k in allowed}
        if 'guests' in clean:
            clean['guests'] = max(int(clean['guests'] or 1), 1)
        order.write(clean)
        return order._terminal_dict()

    @api.model
    def terminal_add_line(self, order_id, product_id, qty=1.0, note=False):
        order = self._terminal_order(order_id)
        if order.state in ('paid', 'cancelled'):
            raise UserError(_("Cette commande est clôturée : impossible d'ajouter des articles."))
        product = self.env['product.product'].browse(product_id).exists()
        if not product or not product.sale_ok:
            raise UserError(_("Produit indisponible."))
        existing = order.order_line_ids.filtered(
            lambda l: l.product_id == product and not l.discount and (l.note or False) == (note or False)
            and l.qty > 0)[:1]
        if existing:
            existing.qty += qty
        else:
            self.env['adk.pos.order.line'].create({
                'order_id': order.id, 'product_id': product.id, 'qty': qty, 'note': note or False})
        return order._terminal_dict()

    @api.model
    def terminal_set_line_qty(self, line_id, qty, employee_id, reason='', approver_id=False,
                              approver_pin=False):
        """Change la quantité d'une ligne ; si la ligne est déjà partie en cuisine, la cuisine est prévenue."""
        employee = self._terminal_employee(employee_id, 'server')
        line = self.env['adk.pos.order.line'].browse(line_id).exists()
        if not line:
            raise UserError(_("Ligne introuvable."))
        order = line.order_id
        if order.state in ('paid', 'cancelled'):
            raise UserError(_("Cette commande est clôturée."))
        qty = max(float(qty), 0.0)
        if qty < line.qty_sent:
            if order.config_id.approve_void:
                self._require_manager(employee, approver_id, approver_pin)
            removed = line.qty_sent - qty
            self.env['adk.pos.kitchen.item'].sudo().create({
                'order_id': order.id, 'line_id': line.id if qty > 0 else False,
                'product_id': line.product_id.id, 'name': line.product_id.display_name,
                'qty': removed, 'note': reason or False, 'course': line.course or 'other',
                'station_id': self.env['adk.pos.station'].sudo()._get_for_product(
                    order.config_id, line.product_id).id or False,
                'batch_no': order.send_count or 1, 'state': 'cancelled',
            })
            order.message_post(body=_("%s a retiré %s × %s après envoi en préparation. Motif : %s") % (
                employee.name, removed, line.product_id.display_name, reason or _("non précisé")))
            line.qty_sent = qty
        if qty <= 0:
            line.unlink()
        else:
            line.qty = qty
        return order._terminal_dict()

    @api.model
    def terminal_update_line(self, line_id, vals, employee_id, approver_id=False, approver_pin=False):
        employee = self._terminal_employee(employee_id, 'server')
        line = self.env['adk.pos.order.line'].browse(line_id).exists()
        if not line:
            raise UserError(_("Ligne introuvable."))
        order = line.order_id
        if order.state in ('paid', 'cancelled'):
            raise UserError(_("Cette commande est clôturée."))
        allowed = {'note', 'course', 'discount', 'price_unit'}
        clean = {k: v for k, v in (vals or {}).items() if k in allowed}
        if 'discount' in clean:
            clean['discount'] = min(max(float(clean['discount'] or 0.0), 0.0), 100.0)
            if clean['discount'] > order.config_id.approve_discount_above:
                self._require_manager(employee, approver_id, approver_pin)
        if 'price_unit' in clean:
            if not order.config_id.allow_price_change:
                raise UserError(_("La modification du prix n'est pas autorisée sur ce point de vente."))
            self._require_manager(employee, approver_id, approver_pin)
            clean['price_unit'] = max(float(clean['price_unit'] or 0.0), 0.0)
        if clean.get('discount') or clean.get('price_unit') is not None:
            order.message_post(body=_("%s a modifié la ligne « %s » : %s") % (
                employee.name, line.product_id.display_name, clean))
        line.write(clean)
        return order._terminal_dict()

    @api.model
    def terminal_apply_discount(self, order_id, percent, employee_id, approver_id=False,
                                approver_pin=False):
        employee = self._terminal_employee(employee_id, 'server')
        order = self._terminal_order(order_id)
        if order.state in ('paid', 'cancelled'):
            raise UserError(_("Cette commande est clôturée."))
        percent = min(max(float(percent or 0.0), 0.0), 100.0)
        if percent > order.config_id.approve_discount_above:
            self._require_manager(employee, approver_id, approver_pin)
        order.order_line_ids.write({'discount': percent})
        order.message_post(body=_("%s a appliqué une remise globale de %s %%.") % (employee.name, percent))
        return order._terminal_dict()

    @api.model
    def terminal_send(self, order_id, employee_id):
        self._terminal_employee(employee_id, 'server')
        order = self._terminal_order(order_id)
        batch = order._send_to_kitchen()
        return {'batch': batch, 'order': order._terminal_dict()}

    @api.model
    def terminal_go_billing(self, order_id, employee_id):
        self._terminal_employee(employee_id, 'server')
        order = self._terminal_order(order_id)
        if order.has_unsent_lines():
            order._send_to_kitchen()
        order.action_go_billing()
        return order._terminal_dict()

    def has_unsent_lines(self):
        self.ensure_one()
        return any(l.qty > l.qty_sent for l in self.order_line_ids)

    @api.model
    def terminal_cancel_order(self, order_id, employee_id, reason='', approver_id=False,
                              approver_pin=False):
        employee = self._terminal_employee(employee_id, 'server')
        order = self._terminal_order(order_id)
        sent = any(l.qty_sent > 0 for l in order.order_line_ids)
        if sent and order.config_id.approve_void:
            self._require_manager(employee, approver_id, approver_pin)
        order._cancel(reason=reason, employee=employee)
        return True

    @api.model
    def terminal_discard_if_empty(self, order_id):
        """Supprime une commande vide laissée par un serveur (sans droit de suppression direct)."""
        order = self.sudo().browse(order_id).exists()
        if order and order.state == 'draft' and not order.order_line_ids and not order.payment_ids:
            order.unlink()
        return True

    @api.model
    def terminal_pay(self, order_id, employee_id, payments, tip=0.0):
        """Enregistre un ou plusieurs paiements ; clôture la commande si tout est réglé."""
        employee = self._terminal_employee(employee_id, 'cashier')
        order = self._terminal_order(order_id)
        if order.state in ('paid', 'cancelled'):
            raise UserError(_("Cette commande est déjà clôturée."))
        if not order.order_line_ids:
            raise UserError(_("Impossible d'encaisser une commande vide."))
        if order.session_id.state != 'opened':
            raise UserError(_("La session de caisse de cette commande est fermée."))
        order = order.sudo()
        if order.state == 'draft' or order.has_unsent_lines():
            if order.config_id.pos_type not in ('retail', 'boutique'):
                order._send_to_kitchen()
        if tip is not None and order.config_id.tip_enabled:
            order.tip_amount = max(float(tip or 0.0), 0.0)
        allowed_methods = order.config_id.payment_method_ids
        Payment = self.env['adk.pos.payment'].sudo()
        remaining = order.amount_due
        for pay in payments or []:
            method = self.env['adk.pos.payment.method'].browse(pay.get('method_id')).exists()
            if not method or method not in allowed_methods:
                raise UserError(_("Mode de paiement non autorisé sur ce point de vente."))
            amount = float(pay.get('amount') or 0.0)
            if amount <= 0:
                continue
            reference = (pay.get('reference') or '').strip()
            if method.is_credit and not order.partner_id:
                raise UserError(_("« %s » : sélectionnez d'abord le client (bouton « Client ») : "
                                  "un paiement à crédit doit être rattaché à un client.") % method.name)
            if method.requires_reference and not reference:
                raise UserError(_("« %s » : la référence / pièce justificative est obligatoire.") % method.name)
            if order.currency_id.is_zero(remaining):
                raise UserError(_("Cette commande est déjà entièrement réglée."))
            if method.is_cash:
                applied = min(amount, remaining)
            else:
                if amount > remaining + 1e-6:
                    raise UserError(_("« %s » : le montant dépasse le reste à payer (%s).")
                                    % (method.name, remaining))
                applied = amount
            Payment.create({
                'order_id': order.id, 'payment_method_id': method.id,
                'amount': applied, 'tendered': amount, 'reference': reference or False,
                'cashier_id': employee.id,
            })
            remaining -= applied
        order._finalize_payment(employee)
        return {
            'paid': order.state == 'paid',
            'change': order.amount_change,
            'order': order._terminal_dict(),
        }

    @api.model
    def terminal_transfer_table(self, order_id, table_id, employee_id):
        """Déplace une commande vers une autre table ; fusionne si la table est déjà occupée."""
        employee = self._terminal_employee(employee_id, 'server')
        order = self._terminal_order(order_id)
        table = self.env['adk.pos.table'].browse(table_id).exists()
        if not table or table.config_id != order.config_id:
            raise UserError(_("Table introuvable pour ce point de vente."))
        if order.state in ('paid', 'cancelled'):
            raise UserError(_("Cette commande est clôturée."))
        if order.table_id == table:
            return order.id
        target = self.sudo().search([('table_id', '=', table.id), ('state', 'in', OPEN_STATES)],
                                    limit=1, order='id')
        old_table = order.table_id.name or '-'
        if target:
            order.order_line_ids.write({'order_id': target.id})
            order.kitchen_item_ids.sudo().write({'order_id': target.id})
            target.write({'guests': target.guests + order.guests,
                          'send_count': max(target.send_count, order.send_count)})
            target.message_post(body=_("%s a fusionné l'addition de la table %s (%s) avec celle-ci.") % (
                employee.name, old_table, order.name))
            order.sudo().unlink()
            return target.id
        order.write({'table_id': table.id})
        if table.is_reserved:
            table.sudo().write({'is_reserved': False, 'reservation_note': ''})
        order.message_post(body=_("%s a déplacé la commande de la table %s vers %s.") % (
            employee.name, old_table, table.name))
        return order.id

    @api.model
    def terminal_split_order(self, order_id, splits, employee_id):
        """Sépare l'addition : les lignes (ou quantités) choisies partent dans une nouvelle commande."""
        employee = self._terminal_employee(employee_id, 'server')
        order = self._terminal_order(order_id)
        if order.state in ('paid', 'cancelled'):
            raise UserError(_("Cette commande est clôturée."))
        if order.payment_ids:
            raise UserError(_("Impossible de séparer une addition qui a déjà reçu un paiement."))
        Line = self.env['adk.pos.order.line']
        chosen = []
        for split in splits or []:
            line = Line.browse(split.get('line_id')).exists()
            qty = float(split.get('qty') or 0.0)
            if line and line.order_id == order and qty > 0:
                chosen.append((line, min(qty, line.qty)))
        if not chosen:
            raise UserError(_("Sélectionnez au moins un article à séparer."))
        if all(abs(qty - line.qty) < 1e-6 for line, qty in chosen) and len(chosen) == len(order.order_line_ids):
            raise UserError(_("Vous ne pouvez pas déplacer tous les articles : il resterait une commande vide."))
        new_order = order.copy({
            'order_line_ids': [], 'state': order.state,
            'send_count': order.send_count, 'guests': 1, 'note': False,
        })
        for line, qty in chosen:
            if abs(qty - line.qty) < 1e-6:
                line.order_id = new_order.id
                continue
            sent_moved = min(line.qty_sent, qty)
            line.write({'qty': line.qty - qty, 'qty_sent': line.qty_sent - sent_moved})
            line.copy({
                'order_id': new_order.id, 'qty': qty, 'qty_sent': sent_moved,
                'name': line.name, 'price_unit': line.price_unit,
                'tax_ids': [(6, 0, line.tax_ids.ids)], 'course': line.course,
            })
        order.message_post(body=_("%s a séparé l'addition : nouvelle commande %s.") % (
            employee.name, new_order.name))
        return new_order.id

    @api.model
    def terminal_refund(self, order_id, lines, employee_id, reason='', method_id=False, session_id=False,
                        approver_id=False, approver_pin=False):
        employee = self._terminal_employee(employee_id, 'cashier')
        order = self._terminal_order(order_id)
        if order.config_id.approve_refund:
            self._require_manager(employee, approver_id, approver_pin)
        session = self.env['adk.pos.session'].browse(session_id).exists() if session_id else False
        if not session:
            session = self.env['adk.pos.session'].search([
                ('config_id', '=', order.config_id.id), ('state', '=', 'opened')], limit=1)
        if not session:
            raise UserError(_("Ouvrez d'abord une session de caisse pour effectuer un remboursement."))
        method = self.env['adk.pos.payment.method'].browse(method_id).exists() if method_id else False
        qtys = {int(l['line_id']): float(l['qty']) for l in (lines or [])}
        refund = order._create_refund(qtys, reason, method, employee, session)
        return refund._terminal_dict()

    @api.model
    def terminal_create_from_reservation(self, reservation_id, employee_id, session_id, table_id=False):
        employee = self._terminal_employee(employee_id, 'server')
        reservation = self.env['adk.pos.reservation'].browse(reservation_id).exists()
        if not reservation or reservation.state != 'confirmed':
            raise UserError(_("Cette réservation n'est plus active."))
        table = self.env['adk.pos.table'].browse(table_id).exists() if table_id else reservation.table_ids[:1]
        order_id = self.terminal_create_order(
            reservation.config_id.id, session_id, employee.id, table.id if table else False,
            'dine_in', reservation.guests, force_new=False)
        order = self.browse(order_id)
        order.write({
            'reservation_id': reservation.id, 'partner_id': reservation.partner_id.id or False,
            'customer_phone': reservation.phone or False, 'guests': reservation.guests,
            'note': reservation.note or False,
        })
        reservation.sudo().write({'state': 'seated', 'order_id': order.id})
        return order.id
