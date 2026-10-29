# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AdkPosStation(models.Model):
    _name = 'adk.pos.station'
    _description = "Poste de préparation (Cuisine, Bar, Pâtisserie…)"
    _order = 'sequence, name'

    name = fields.Char(string="Nom du poste", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer(string="Couleur")
    config_ids = fields.Many2many(
        'adk.pos.config', string="Points de vente",
        help="Points de vente qui envoient leurs commandes vers ce poste.")
    category_ids = fields.Many2many(
        'product.category', string="Catégories de produits préparées ici",
        help="Les articles de ces catégories (et de leurs sous-catégories) sont envoyés à ce poste. "
             "Un poste sans catégorie reçoit tous les articles qui n'ont pas de poste dédié.")

    pos_type = fields.Selection([
        ('restaurant', 'Restaurant'), ('cafe', 'Café'), ('bar', 'Bar'),
        ('fastfood', 'Fast-food'), ('retail', 'Commerce de détail'), ('boutique', 'Boutique'),
    ], string="Type de point de vente",
        help="Poste proposé automatiquement à la création d'un point de vente de ce type (assistant de démarrage).")
    user_ids = fields.Many2many(
        'res.users', 'adk_pos_station_user_rel', 'station_id', 'user_id',
        string="Accès réservé à",
        help="Personnel autorisé à voir cet écran de préparation. "
             "Laissez vide pour que tout le personnel du point de vente puisse le voir. "
             "Les managers voient toujours tous les écrans.")

    @api.depends('name', 'pos_type')
    def _compute_display_name(self):
        labels = dict(self._fields['pos_type'].selection)
        for station in self:
            station.display_name = "%s (%s)" % (station.name, labels[station.pos_type]) \
                if station.pos_type else station.name

    @api.model
    def get_stations_for_user(self, config_id):
        """Écrans de préparation visibles par l'utilisateur connecté pour un point de vente."""
        user = self.env.user
        stations = self.sudo().search([('config_ids', 'in', config_id)], order='sequence, id')
        if user.has_group('adk_pos.group_adk_pos_manager'):
            return [s._board_dict() for s in stations]
        mine = user.sudo().adk_pos_station_ids
        return [s._board_dict() for s in stations
                if (not s.user_ids or user in s.user_ids) and (not mine or s in mine)]

    def _board_dict(self):
        self.ensure_one()
        return {
            'id': self.id, 'name': self.name, 'color': (self.color or 0) % 12,
            'categories': self.category_ids.mapped('name'),
        }

    def _get_for_product(self, config, product):
        """Retourne le poste de préparation chargé d'un produit pour un point de vente."""
        stations = self.search([('config_ids', 'in', config.id)], order='sequence, id')
        if not stations:
            return self.browse()
        categ = product.categ_id
        categ_ids = set()
        if categ and categ.parent_path:
            categ_ids = {int(i) for i in categ.parent_path.split('/') if i}
        for station in stations:
            if station.category_ids and categ_ids & set(station.category_ids.ids):
                return station
        for station in stations:
            if not station.category_ids:
                return station
        return self.browse()
