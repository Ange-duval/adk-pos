# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AdkPosComboLine(models.Model):
    _name = 'adk.pos.combo.line'
    _description = "Produit inclus dans un combo"

    combo_id = fields.Many2one('adk.pos.combo', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string="Produit", required=True)
    qty = fields.Float(string="Quantité", default=1.0)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.mapped('combo_id')._sync_product()
        return lines

    def write(self, vals):
        res = super().write(vals)
        self.mapped('combo_id')._sync_product()
        return res

    def unlink(self):
        combos = self.mapped('combo_id')
        res = super().unlink()
        combos._sync_product()
        return res


class AdkPosCombo(models.Model):
    _name = 'adk.pos.combo'
    _description = "Combo / Menu Point de Vente"
    _order = 'name'

    name = fields.Char(string="Nom du combo", required=True)
    price = fields.Float(string="Prix fixe du combo", required=True)
    active = fields.Boolean(default=True)
    image_1920 = fields.Image(string="Image")
    combo_line_ids = fields.One2many('adk.pos.combo.line', 'combo_id', string="Produits inclus")
    product_id = fields.Many2one('product.product', string="Produit de vente associé",
                                 readonly=True, copy=False,
                                 help="Généré et synchronisé automatiquement : c'est cet "
                                      "article qui apparaît dans le catalogue du terminal.")
    adk_pos_config_ids = fields.Many2many('adk.pos.config', string="Disponible dans ces points de vente")

    def _sync_product(self):
        Product = self.env['product.product'].sudo()
        for combo in self.exists():
            description = ", ".join(
                f"{line.qty:g}x {line.product_id.name}" for line in combo.combo_line_ids)
            vals = {
                'name': combo.name, 'list_price': combo.price, 'sale_ok': True,
                'description_sale': description, 'image_1920': combo.image_1920 or False,
                'adk_pos_config_ids': [(6, 0, combo.adk_pos_config_ids.ids)],
                'adk_pos_course': 'main',
            }
            if combo.product_id:
                combo.product_id.sudo().write(vals)
            else:
                combo.product_id = Product.create(vals).id

    @api.model_create_multi
    def create(self, vals_list):
        combos = super().create(vals_list)
        combos._sync_product()
        return combos

    def write(self, vals):
        res = super().write(vals)
        if any(f in vals for f in ('name', 'price', 'adk_pos_config_ids', 'combo_line_ids', 'image_1920')):
            self._sync_product()
        if 'active' in vals:
            self.mapped('product_id').sudo().write({'active': vals['active']})
        return res

    def unlink(self):
        products = self.mapped('product_id')
        res = super().unlink()
        products.sudo().write({'active': False})
        return res
