# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class AdkPosCashMove(models.Model):
    _name = 'adk.pos.cash.move'
    _description = "Mouvement de caisse (entrée / sortie)"
    _order = 'create_date desc'

    session_id = fields.Many2one('adk.pos.session', string="Session", required=True, ondelete='cascade')
    config_id = fields.Many2one(related='session_id.config_id', store=True)
    move_type = fields.Selection([('in', 'Entrée'), ('out', 'Sortie')], required=True, default='in')
    amount = fields.Monetary(required=True, currency_field='currency_id')
    currency_id = fields.Many2one(related='session_id.currency_id')
    reason = fields.Char(string="Motif", required=True)
    user_id = fields.Many2one('res.users', string="Effectué par", required=True,
                              default=lambda self: self.env.user)

    @api.constrains('amount')
    def _check_amount(self):
        for move in self:
            if move.amount <= 0:
                raise UserError(_("Le montant d'un mouvement de caisse doit être strictement positif."))

    @api.model_create_multi
    def create(self, vals_list):
        sessions = self.env['adk.pos.session'].browse([v.get('session_id') for v in vals_list if v.get('session_id')])
        if any(s.state != 'opened' for s in sessions):
            raise UserError(_("Les mouvements de caisse ne sont possibles que sur une session ouverte."))
        return super().create(vals_list)
