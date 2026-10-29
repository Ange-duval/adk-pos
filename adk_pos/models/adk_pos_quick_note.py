# -*- coding: utf-8 -*-
from odoo import fields, models


class AdkPosQuickNote(models.Model):
    _name = 'adk.pos.quick.note'
    _description = "Note rapide (ex : sans oignons)"
    _order = 'sequence, name'

    name = fields.Char(string="Note", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
