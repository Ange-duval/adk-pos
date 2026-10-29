# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    adk_pos_has_image = fields.Boolean(
        string="A une image", compute='_compute_adk_pos_has_image',
        help="Permet au terminal de savoir s'il doit afficher la photo ou l'initiale colorée.")

    @api.depends('image_1920')
    def _compute_adk_pos_has_image(self):
        for product in self:
            product.adk_pos_has_image = bool(product.image_1920)
