# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

COURSES = [
    ('drink', 'Boisson'),
    ('starter', 'Entrée'),
    ('main', 'Plat'),
    ('dessert', 'Dessert'),
    ('other', 'Autre'),
]


class AdkPosOrderLine(models.Model):
    _name = 'adk.pos.order.line'
    _description = "Ligne de commande Point de Vente"
    _order = 'id'

    order_id = fields.Many2one('adk.pos.order', string="Commande", required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string="Produit", required=True)
    name = fields.Char(string="Description", compute='_compute_name', store=True, readonly=False)
    qty = fields.Float(string="Quantité", default=1.0)
    qty_sent = fields.Float(string="Quantité envoyée en préparation", copy=False, default=0.0)
    price_unit = fields.Float(string="Prix unitaire", compute='_compute_price_unit',
                              store=True, readonly=False)
    discount = fields.Float(string="Remise (%)", default=0.0)
    tax_ids = fields.Many2many('account.tax', string="Taxes", compute='_compute_tax_ids',
                               store=True, readonly=False)
    note = fields.Char(string="Note (ex: sans oignons)")
    course = fields.Selection(COURSES, string="Service", compute='_compute_course',
                              store=True, readonly=False)
    uom_id = fields.Many2one(related='product_id.uom_id', string="UdM")

    refunded_line_id = fields.Many2one('adk.pos.order.line', string="Ligne remboursée", copy=False,
                                       ondelete='set null', index=True)
    refund_line_ids = fields.One2many('adk.pos.order.line', 'refunded_line_id')
    refunded_qty = fields.Float(string="Quantité remboursée", compute='_compute_refunded_qty', store=True)

    currency_id = fields.Many2one(related='order_id.currency_id')
    price_subtotal = fields.Monetary(compute='_compute_amount', store=True,
                                     string="Hors taxes", currency_field='currency_id')
    price_tax = fields.Monetary(compute='_compute_amount', store=True,
                                string="Taxes", currency_field='currency_id')
    price_total = fields.Monetary(compute='_compute_amount', store=True,
                                  string="Toutes taxes comprises", currency_field='currency_id')
    discount_amount = fields.Monetary(compute='_compute_amount', store=True,
                                      string="Montant de la remise", currency_field='currency_id')
    margin = fields.Monetary(compute='_compute_amount', store=True,
                             string="Marge", currency_field='currency_id')

    @api.depends('product_id')
    def _compute_name(self):
        for line in self:
            line.name = line.product_id.display_name or line.name

    @api.depends('product_id')
    def _compute_price_unit(self):
        for line in self:
            line.price_unit = line.product_id.lst_price

    @api.depends('product_id', 'order_id.order_type', 'order_id.config_id.takeaway_fiscal_position_id')
    def _compute_tax_ids(self):
        for line in self:
            company = line.order_id.company_id or self.env.company
            taxes = line.product_id.taxes_id.filtered(
                lambda t, c=company: not t.company_id or t.company_id == c)
            fpos = line.order_id.config_id.takeaway_fiscal_position_id
            if fpos and line.order_id.order_type in ('takeaway', 'delivery'):
                taxes = fpos.map_tax(taxes)
            line.tax_ids = taxes

    @api.depends('product_id')
    def _compute_course(self):
        for line in self:
            line.course = line.product_id.product_tmpl_id.adk_pos_course or 'other'

    @api.depends('refund_line_ids.qty')
    def _compute_refunded_qty(self):
        for line in self:
            line.refunded_qty = -sum(line.refund_line_ids.mapped('qty'))

    @api.depends('qty', 'price_unit', 'discount', 'tax_ids', 'product_id')
    def _compute_amount(self):
        for line in self:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            subtotal = price * line.qty
            if line.tax_ids:
                taxes = line.tax_ids.compute_all(
                    price, quantity=line.qty, currency=line.order_id.currency_id,
                    product=line.product_id, partner=line.order_id.partner_id)
            else:
                taxes = {'total_excluded': subtotal, 'total_included': subtotal}
            line.price_subtotal = taxes['total_excluded']
            line.price_total = taxes['total_included']
            line.price_tax = taxes['total_included'] - taxes['total_excluded']
            line.discount_amount = line.price_unit * line.qty * (line.discount or 0.0) / 100.0
            cost = (line.product_id.standard_price or 0.0) * line.qty
            line.margin = taxes['total_excluded'] - cost

    # ------------------------------------------------------------------
    # Une commande validée (payée / annulée) n'est plus modifiable
    # ------------------------------------------------------------------
    def _check_order_open(self):
        if self.env.context.get('adk_force'):
            return
        for line in self:
            if line.order_id.state in ('paid', 'cancelled'):
                raise UserError(_("La commande %s est validée : ses lignes ne sont plus modifiables.")
                                % line.order_id.name)

    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env['adk.pos.order'].browse([v['order_id'] for v in vals_list if v.get('order_id')])
        if not self.env.context.get('adk_force'):
            for order in orders:
                if order.state in ('paid', 'cancelled') and not order.is_refund:
                    raise UserError(_("La commande %s est validée : on ne peut plus y ajouter d'articles.")
                                    % order.name)
        return super().create(vals_list)

    def write(self, vals):
        if set(vals) - {'qty_sent'}:
            self._check_order_open()
        return super().write(vals)

    def unlink(self):
        self._check_order_open()
        return super().unlink()
