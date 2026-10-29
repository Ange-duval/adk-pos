# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AdkPosPayment(models.Model):
    _name = 'adk.pos.payment'
    _description = "Paiement Point de Vente"
    _order = 'date desc, id desc'

    order_id = fields.Many2one('adk.pos.order', string="Commande", required=True,
                               ondelete='cascade', index=True)
    session_id = fields.Many2one(related='order_id.session_id', store=True, index=True)
    config_id = fields.Many2one(related='order_id.config_id', store=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True)
    currency_id = fields.Many2one(related='order_id.currency_id')
    payment_method_id = fields.Many2one('adk.pos.payment.method', string="Mode de paiement",
                                        required=True, ondelete='restrict')
    is_cash = fields.Boolean(related='payment_method_id.is_cash', store=True)
    amount = fields.Monetary(string="Montant imputé", currency_field='currency_id', required=True,
                             help="Montant réellement imputé sur la commande "
                                  "(négatif pour un remboursement).")
    tendered = fields.Monetary(string="Montant remis", currency_field='currency_id',
                               help="Montant remis par le client (espèces) avant rendu de monnaie.")
    change_given = fields.Monetary(string="Monnaie rendue", compute='_compute_change_given',
                                   store=True, currency_field='currency_id')
    reference = fields.Char(string="Référence / Pièce justificative")
    cashier_id = fields.Many2one('res.users', string="Encaissé par", default=lambda self: self.env.user)
    date = fields.Datetime(default=fields.Datetime.now, string="Date")
    is_refund = fields.Boolean(compute='_compute_is_refund', store=True)

    @api.depends('amount', 'tendered', 'is_cash')
    def _compute_change_given(self):
        for pay in self:
            if pay.is_cash and pay.amount > 0 and pay.tendered > pay.amount:
                pay.change_given = pay.tendered - pay.amount
            else:
                pay.change_given = 0.0

    @api.depends('amount')
    def _compute_is_refund(self):
        for pay in self:
            pay.is_refund = pay.amount < 0

    def write(self, vals):
        if not self.env.context.get('adk_force') and set(vals) & {'amount', 'payment_method_id', 'order_id', 'tendered'}:
            for pay in self:
                if pay.order_id.state in ('paid', 'cancelled'):
                    raise UserError(_("Le paiement d'une commande validée n'est plus modifiable."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('adk_force'):
            for pay in self:
                if pay.order_id.state in ('paid', 'cancelled'):
                    raise UserError(_("Le paiement d'une commande validée ne peut pas être supprimé."))
        return super().unlink()
