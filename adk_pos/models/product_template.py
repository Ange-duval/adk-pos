# -*- coding: utf-8 -*-
from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    adk_pos_config_ids = fields.Many2many(
        'adk.pos.config', string="Disponible dans ces points de vente",
        help="Restreint ce produit aux points de vente sélectionnés. "
             "Si vide, le produit est visible dans tous les points de vente ADK POS.")
    adk_pos_course = fields.Selection([
        ('drink', 'Boisson'),
        ('starter', 'Entrée'),
        ('main', 'Plat'),
        ('dessert', 'Dessert'),
        ('other', 'Autre'),
    ], string="Service (ordre de sortie)", default='other',
        help="Permet à la cuisine de préparer et d'annoncer les plats dans le bon ordre.")
    adk_pos_favorite = fields.Boolean(
        string="Favori du terminal",
        help="Affiche ce produit dans l'onglet « Favoris » du terminal pour un accès rapide.")
