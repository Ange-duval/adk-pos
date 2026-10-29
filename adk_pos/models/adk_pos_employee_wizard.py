# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.exceptions import UserError


class AdkPosEmployeeWizard(models.TransientModel):
    _name = 'adk.pos.employee.wizard'
    _description = "Créer un membre du personnel Point de Vente"

    name = fields.Char(string="Nom complet", required=True)
    login = fields.Char(string="Identifiant de connexion (email)", required=True,
                         help="Utilisé pour se connecter à Odoo. Peut être une adresse email fictive "
                              "si le collaborateur n'utilise que le code PIN sur les tablettes.")
    role = fields.Selection([
        ('server', 'Serveur'),
        ('cashier', 'Caissier'),
        ('manager', 'Manager'),
    ], string="Rôle", required=True, default='server')
    config_id = fields.Many2one('adk.pos.config', string="Point de vente", required=True)
    pin = fields.Char(string="Code PIN (4 à 6 chiffres)", required=True)
    pin_confirm = fields.Char(string="Confirmer le code PIN", required=True)

    def action_confirm(self):
        self.ensure_one()
        if not self.pin.isdigit() or not (4 <= len(self.pin) <= 6):
            raise UserError("Le code PIN doit contenir entre 4 et 6 chiffres.")
        if self.pin != self.pin_confirm:
            raise UserError("Les deux codes PIN saisis ne correspondent pas.")

        Users = self.env['res.users'].sudo()
        existing = Users.search([('login', '=', self.login)], limit=1)
        group_xmlid = {
            'server': 'adk_pos.group_adk_pos_server',
            'cashier': 'adk_pos.group_adk_pos_cashier',
            'manager': 'adk_pos.group_adk_pos_manager',
        }[self.role]
        if existing:
            user = existing
            user.write({'groups_id': [(4, self.env.ref(group_xmlid).id)]})
        else:
            group = self.env.ref(group_xmlid)
            user = Users.create({
                'name': self.name,
                'login': self.login,
                'email': self.login,
                'groups_id': [(4, group.id), (4, self.env.ref('base.group_user').id)],
            })

        user.adk_pos_set_pin(self.pin)

        config = self.config_id.sudo()
        if self.role == 'server':
            config.write({'server_ids': [(4, user.id)]})
        elif self.role == 'cashier':
            config.write({'cashier_ids': [(4, user.id)]})
        else:
            config.write({'manager_id': user.id})

        return {'type': 'ir.actions.act_window_close'}
