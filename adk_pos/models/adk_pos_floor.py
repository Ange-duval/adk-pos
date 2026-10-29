# -*- coding: utf-8 -*-
import base64
import io
import json
import math
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import file_open


def _natural_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', name or '')]


class AdkPosFloor(models.Model):
    _name = 'adk.pos.floor'
    _description = "Salle / Zone du point de vente"
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    config_id = fields.Many2one('adk.pos.config', string="Point de vente",
                                required=True, ondelete='cascade')
    table_ids = fields.One2many('adk.pos.table', 'floor_id', string="Tables")
    table_count = fields.Integer(compute='_compute_table_count')
    background_color = fields.Char(string="Couleur de fond", default="#F5F5F5")
    background_image = fields.Image(
        string="Image de fond (plan de salle)", max_width=1600, max_height=1600,
        help="Importez le plan de votre salle (photo, dessin, capture) : les tables se placent "
             "par-dessus, que vous déplacez ensuite au doigt dans le terminal.")
    canvas_width = fields.Integer(string="Largeur du plan", default=1000)
    canvas_height = fields.Integer(string="Hauteur du plan", default=620)

    def _compute_table_count(self):
        for floor in self:
            floor.table_count = len(floor.table_ids)

    # ------------------------------------------------------------------
    # Image de fond : le plan prend les proportions de l'image
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        floors = super().create(vals_list)
        for floor in floors.filtered('background_image'):
            floor._fit_canvas_to_image()
        return floors

    def write(self, vals):
        res = super().write(vals)
        if 'background_image' in vals:
            for floor in self:
                if floor.background_image:
                    floor._fit_canvas_to_image()
                else:
                    super(AdkPosFloor, floor).write({'canvas_width': 1000, 'canvas_height': 620})
        return res

    def _fit_canvas_to_image(self):
        self.ensure_one()
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(base64.b64decode(self.background_image)))
            w, h = img.size
        except Exception:  # image illisible : on garde le plan tel quel
            return
        if w and h:
            super(AdkPosFloor, self).write({'canvas_width': 1000, 'canvas_height': max(int(1000 * h / w), 300)})

    # ------------------------------------------------------------------
    # Génération et rangement automatiques des tables
    # ------------------------------------------------------------------
    def _grid_slots(self, count):
        """Positions (x, y, taille) bien alignées pour `count` tables dans le plan."""
        self.ensure_one()
        W, H = float(self.canvas_width or 1000), float(self.canvas_height or 620)
        cols = max(int(math.ceil(math.sqrt(count * W / H))), 1)
        rows = max(int(math.ceil(count / float(cols))), 1)
        cell_w, cell_h = W / cols, H / rows
        size = int(max(min(cell_w, cell_h) * 0.72, 56))
        size = int(round(min(size, 130) / 10.0) * 10)
        slots = []
        for i in range(count):
            row, col = divmod(i, cols)
            x = col * cell_w + (cell_w - size) / 2.0
            y = row * cell_h + (cell_h - size) / 2.0
            slots.append((round(x / 10.0) * 10, round(y / 10.0) * 10, size))
        return slots

    def action_arrange_tables(self):
        """Range toutes les tables de la salle en grille régulière (sans chevauchement)."""
        for floor in self:
            tables = floor.table_ids.filtered('active').sorted(key=lambda t: _natural_key(t.name))
            for table, (x, y, size) in zip(tables, floor._grid_slots(len(tables))):
                table.write({'pos_x': x, 'pos_y': y, 'width': size, 'height': size})
        return True

    def generate_tables(self, count, seats=4, shape='square', prefix='T', replace=False):
        """Crée `count` tables numérotées puis range toute la salle."""
        self.ensure_one()
        count = int(count or 0)
        if count < 1 or count > 150:
            raise UserError(_("Indiquez un nombre de tables entre 1 et 150."))
        if replace:
            busy = self.env['adk.pos.order'].sudo().search_count([
                ('table_id', 'in', self.table_ids.ids), ('state', 'in', ('draft', 'sent', 'billing'))])
            if busy:
                raise UserError(_("Des commandes sont en cours sur cette salle : impossible de remplacer les tables."))
            self.table_ids.sudo().write({'active': False})
        existing = self.table_ids.filtered('active')
        start = len(existing)
        Table = self.env['adk.pos.table'].with_context(adk_no_autoplace=True).sudo()
        taken = set(existing.mapped('name'))
        created = 0
        n = start
        while created < count:
            n += 1
            name = "%s%d" % (prefix or 'T', n)
            if name in taken:
                continue
            Table.create({'name': name, 'floor_id': self.id, 'seats': max(int(seats or 4), 1),
                          'shape': shape or 'square'})
            created += 1
        self.invalidate_recordset()
        self.action_arrange_tables()
        return True

    def apply_sample_plan(self, count=14, seats=None, prefix='T'):
        """Pose le plan de salle d'exemple (image réaliste) et place les tables à leur emplacement réel.
        Au-delà des 14 tables du plan, les suivantes sont posées dans les espaces libres."""
        self.ensure_one()
        with file_open('adk_pos/static/img/plan_restaurant.jpg', 'rb') as f:
            self.background_image = base64.b64encode(f.read())
        with file_open('adk_pos/static/img/plan_restaurant_tables.json', 'r') as f:
            layout = json.load(f)
        Table = self.env['adk.pos.table'].with_context(adk_no_autoplace=True).sudo()
        count = max(int(count or 0), 0)
        for i, spec in enumerate(layout[:count], 1):
            Table.create({
                'name': "%s%d" % (prefix, i), 'floor_id': self.id, 'seats': spec['seats'],
                'shape': spec['shape'], 'pos_x': spec['x'], 'pos_y': spec['y'],
                'width': spec['w'], 'height': spec['h'],
            })
        extra = count - len(layout)
        if extra > 0:
            Extra = self.env['adk.pos.table'].sudo()
            for i in range(len(layout) + 1, count + 1):
                Extra.create({'name': "%s%d" % (prefix, i), 'floor_id': self.id, 'seats': seats or 4})
        return True

    def action_open_generate_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Générer les tables"),
            'res_model': 'adk.pos.table.generate.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_floor_id': self.id},
        }

    def _free_spot(self, size=100):
        """Premier emplacement du plan qui ne recouvre aucune table existante."""
        self.ensure_one()
        W, H = self.canvas_width or 1000, self.canvas_height or 620
        tables = self.table_ids.filtered('active')
        step = 20
        y = 20
        while y + size <= H:
            x = 20
            while x + size <= W:
                if not any(x < t.pos_x + t.width and x + size > t.pos_x and
                           y < t.pos_y + t.height and y + size > t.pos_y for t in tables):
                    return x, y
                x += step
            y += step
        return 20, 20
