# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AdkPosRefundWizard(models.TransientModel):
    _name = 'adk.pos.refund.wizard'
    _description = "Rembourser une commande (total ou partiel)"

    order_id = fields.Many2one('adk.pos.order', string="Commande", required=True, readonly=True)
    payment_method_id = fields.Many2one(
        'adk.pos.payment.method', string="Rembourser via", required=True,
        domain="[('id', 'in', allowed_method_ids)]")
    allowed_method_ids = fields.Many2many('adk.pos.payment.method', compute='_compute_allowed_methods')
    reason = fields.Char(string="Motif du remboursement", required=True)
    line_ids = fields.One2many('adk.pos.refund.wizard.line', 'wizard_id', string="Articles")

    @api.depends('order_id')
    def _compute_allowed_methods(self):
        for wiz in self:
            wiz.allowed_method_ids = wiz.order_id.config_id.payment_method_ids

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order = self.env['adk.pos.order'].browse(res.get('order_id') or self.env.context.get('default_order_id'))
        if order:
            res['payment_method_id'] = order.payment_method_id.id
            res['line_ids'] = [(0, 0, {
                'order_line_id': l.id, 'qty_available': l.qty - l.refunded_qty, 'qty': 0.0,
            }) for l in order.order_line_ids if l.qty - l.refunded_qty > 0]
        return res

    def action_confirm(self):
        self.ensure_one()
        user = self.env.user
        if user._adk_pos_role() not in ('cashier', 'manager'):
            raise UserError(_("Seul un caissier ou un manager peut effectuer un remboursement."))
        if self.order_id.config_id.approve_refund and user._adk_pos_role() != 'manager':
            raise UserError(_("Le remboursement doit être validé par un manager."))
        session = self.env['adk.pos.session'].search([
            ('config_id', '=', self.order_id.config_id.id), ('state', '=', 'opened')], limit=1)
        if not session:
            raise UserError(_("Ouvrez d'abord une session de caisse sur ce point de vente."))
        qtys = {l.order_line_id.id: l.qty for l in self.line_ids if l.qty > 0}
        refund = self.order_id._create_refund(qtys, self.reason, self.payment_method_id, user, session)
        return {
            'type': 'ir.actions.act_window', 'res_model': 'adk.pos.order',
            'view_mode': 'form', 'res_id': refund.id,
        }


class AdkPosRefundWizardLine(models.TransientModel):
    _name = 'adk.pos.refund.wizard.line'
    _description = "Ligne de remboursement"

    wizard_id = fields.Many2one('adk.pos.refund.wizard', required=True, ondelete='cascade')
    order_line_id = fields.Many2one('adk.pos.order.line', string="Article", required=True)
    qty_available = fields.Float(string="Remboursable", readonly=True)
    qty = fields.Float(string="Quantité à rembourser")

    @api.constrains('qty')
    def _check_qty(self):
        for line in self:
            if line.qty < 0 or line.qty > line.qty_available + 1e-6:
                raise UserError(_("La quantité à rembourser doit être comprise entre 0 et %s.") % line.qty_available)
