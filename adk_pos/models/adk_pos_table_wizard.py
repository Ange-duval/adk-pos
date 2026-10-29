# -*- coding: utf-8 -*-
from odoo import fields, models


class AdkPosTableGenerateWizard(models.TransientModel):
    _name = 'adk.pos.table.generate.wizard'
    _description = "Générer automatiquement les tables d'une salle"

    floor_id = fields.Many2one('adk.pos.floor', string="Salle", required=True, ondelete='cascade')
    count = fields.Integer(string="Nombre de tables à créer", default=10, required=True)
    seats = fields.Integer(string="Places par table", default=4)
    shape = fields.Selection([('square', 'Carrée'), ('round', 'Ronde'), ('rectangle', 'Rectangulaire')],
                             string="Forme", default='square')
    prefix = fields.Char(string="Préfixe du nom", default='T', help="T → T1, T2, T3…")
    replace = fields.Boolean(string="Remplacer les tables existantes",
                             help="Désactive les tables actuelles de la salle avant d'en créer de nouvelles.")

    def action_generate(self):
        self.ensure_one()
        self.floor_id.generate_tables(self.count, self.seats, self.shape, self.prefix, self.replace)
        return {'type': 'ir.actions.act_window_close'}
