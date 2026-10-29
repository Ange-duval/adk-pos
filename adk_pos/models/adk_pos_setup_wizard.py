# -*- coding: utf-8 -*-
import math

from odoo import _, fields, models
from odoo.exceptions import UserError


class AdkPosSetupWizard(models.TransientModel):
    _name = 'adk.pos.setup.wizard'
    _description = "Assistant de démarrage : créer mon premier point de vente"

    name = fields.Char(string="Nom du point de vente", required=True, help="Ex : Restaurant Le Bon Goût")
    pos_type = fields.Selection([
        ('restaurant', 'Restaurant'), ('cafe', 'Café'), ('bar', 'Bar'),
        ('fastfood', 'Fast-food'), ('retail', 'Commerce de détail (épicerie…)'),
        ('boutique', 'Boutique (vêtements, chaussures…)'),
    ], string="Type d'activité", required=True, default='restaurant')
    table_count = fields.Integer(string="Nombre de tables", default=8,
                                 help="Les tables sont créées en grille ; vous les déplacez ensuite au doigt dans le terminal.")
    seats = fields.Integer(string="Places par table", default=4)
    use_sample_plan = fields.Boolean(
        string="Utiliser le plan de salle d'exemple (image réaliste, tables déjà placées)", default=True,
        help="Pose un vrai plan de salle (cuisine, bar, entrée, fenêtres) et place les tables à leur emplacement. "
             "Vous pourrez le remplacer par l'image de votre propre salle (Salles / Tables ▸ Image de fond).")
    create_stations = fields.Boolean(string="Activer les écrans de préparation (Cuisine, Bar…)", default=True)
    create_notes = fields.Boolean(string="Créer des notes rapides courantes (sans oignons, bien cuit…)", default=True)
    pin = fields.Char(string="Votre code PIN (4 à 6 chiffres)", required=True)
    pin_confirm = fields.Char(string="Confirmer le code PIN", required=True)
    uses_tables = fields.Boolean(compute='_compute_uses_tables')

    def _compute_uses_tables(self):
        for w in self:
            w.uses_tables = w.pos_type not in ('retail', 'boutique')

    def action_confirm(self):
        self.ensure_one()
        if not self.pin.isdigit() or not (4 <= len(self.pin) <= 6):
            raise UserError(_("Le code PIN doit contenir entre 4 et 6 chiffres."))
        if self.pin != self.pin_confirm:
            raise UserError(_("Les deux codes PIN ne correspondent pas."))
        if not self.env.user.has_group('adk_pos.group_adk_pos_manager'):
            raise UserError(_("Réservé aux managers du point de vente."))
        user = self.env.user.sudo()
        methods = self.env['adk.pos.payment.method'].search([])
        config = self.env['adk.pos.config'].create({
            'name': self.name, 'pos_type': self.pos_type, 'manager_id': user.id,
            'cashier_ids': [(4, user.id)], 'server_ids': [(4, user.id)],
            'payment_method_ids': [(6, 0, methods.ids)],
        })
        user.adk_pos_set_pin(self.pin)
        if self.pos_type not in ('retail', 'boutique') and self.table_count > 0:
            floor = self.env['adk.pos.floor'].create({'name': _("Salle"), 'config_id': config.id, 'sequence': 1})
            if self.use_sample_plan:
                floor.apply_sample_plan(min(self.table_count, 80), max(self.seats, 1))
            else:
                floor.generate_tables(min(self.table_count, 80), max(self.seats, 1),
                                      'round' if self.seats <= 2 else 'square')
        root = self.env.ref('adk_pos.categ_%s' % self.pos_type, raise_if_not_found=False)
        if root:
            config.write({'limit_categories': True, 'category_ids': [(6, 0, root.ids)]})
        if self.create_stations and self.pos_type not in ('retail', 'boutique'):
            stations = self.env['adk.pos.station'].search([('pos_type', '=', self.pos_type)])
            stations.write({'config_ids': [(4, config.id)]})
            if not stations:
                Station = self.env['adk.pos.station']
                Station.create({'name': _("Cuisine"), 'color': 2, 'sequence': 1, 'config_ids': [(6, 0, config.ids)]})
                Station.create({'name': _("Bar"), 'color': 4, 'sequence': 2, 'config_ids': [(6, 0, config.ids)]})
        if self.create_notes:
            Note = self.env['adk.pos.quick.note']
            notes = Note.create([{'name': n, 'sequence': i} for i, n in enumerate(
                [_("Sans oignons"), _("Sans piment"), _("Bien pimenté"), _("Bien cuit"), _("Saignant"),
                 _("À emporter"), _("Allergie : voir note")], 1)])
            config.quick_note_ids = [(6, 0, notes.ids)]
        return {'type': 'ir.actions.client', 'tag': 'adk_pos_terminal', 'target': 'main'}
