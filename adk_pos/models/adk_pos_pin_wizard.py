# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.exceptions import UserError


class AdkPosPinWizard(models.TransientModel):
    _name = 'adk.pos.pin.wizard'
    _description = "Définir le code PIN d'un collaborateur"

    user_id = fields.Many2one('res.users', string="Collaborateur", required=True)
    pin = fields.Char(string="Nouveau code PIN (4 à 6 chiffres)", required=True)
    pin_confirm = fields.Char(string="Confirmer le code PIN", required=True)

    def action_confirm(self):
        self.ensure_one()
        if not self.pin.isdigit() or not (4 <= len(self.pin) <= 6):
            raise UserError("Le code PIN doit contenir entre 4 et 6 chiffres.")
        if self.pin != self.pin_confirm:
            raise UserError("Les deux codes PIN saisis ne correspondent pas.")
        self.user_id.adk_pos_set_pin(self.pin)
        return {'type': 'ir.actions.act_window_close'}
