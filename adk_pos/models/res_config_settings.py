# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    adk_pos_default_type = fields.Selection([
        ('restaurant', 'Restaurant'),
        ('cafe', 'Café'),
        ('bar', 'Bar'),
        ('fastfood', 'Fast-food'),
        ('retail', 'Commerce de détail (épicerie, supérette)'),
        ('boutique', 'Boutique (vêtements, chaussures, accessoires)'),
    ], string="Type de point de vente par défaut",
        config_parameter='adk_pos.default_type', default='restaurant')
