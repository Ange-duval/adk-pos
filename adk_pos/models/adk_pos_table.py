# -*- coding: utf-8 -*-
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class AdkPosTable(models.Model):
    _name = 'adk.pos.table'
    _description = "Table du point de vente"
    _order = 'floor_id, name'

    name = fields.Char(required=True)
    floor_id = fields.Many2one('adk.pos.floor', string="Salle", required=True, ondelete='cascade')
    config_id = fields.Many2one(related='floor_id.config_id', store=True, string="Point de vente")
    seats = fields.Integer(string="Nombre de places", default=4)
    shape = fields.Selection([
        ('square', 'Carrée'), ('round', 'Ronde'), ('rectangle', 'Rectangulaire'),
    ], default='square', string="Forme")
    pos_x = fields.Float(string="Position X", default=50)
    pos_y = fields.Float(string="Position Y", default=50)
    width = fields.Float(string="Largeur", default=100)
    height = fields.Float(string="Hauteur", default=100)
    active = fields.Boolean(default=True)

    # Marquage manuel conservé pour compatibilité (la vraie gestion passe par les réservations)
    is_reserved = fields.Boolean(string="Réservée manuellement", default=False)
    reservation_note = fields.Char(string="Note de réservation")

    order_ids = fields.One2many('adk.pos.order', 'table_id', string="Commandes")
    reservation_ids = fields.Many2many(
        'adk.pos.reservation', 'adk_pos_reservation_table_rel', 'table_id', 'reservation_id',
        string="Réservations")

    state = fields.Selection([
        ('free', 'Libre'),
        ('occupied', 'Occupée'),
        ('reserved', 'Réservée'),
        ('billing', 'En facturation'),
    ], string="Statut", compute='_compute_state')
    current_order_id = fields.Many2one('adk.pos.order', string="Commande en cours",
                                       compute='_compute_current_order')

    @api.model_create_multi
    def create(self, vals_list):
        """Une table créée sans position est posée dans un emplacement libre (jamais sur une autre)."""
        if not self.env.context.get('adk_no_autoplace'):
            for vals in vals_list:
                if 'pos_x' in vals or 'pos_y' in vals or not vals.get('floor_id'):
                    continue
                floor = self.env['adk.pos.floor'].browse(vals['floor_id'])
                size = vals.get('width') or 100
                vals['width'] = vals['height'] = vals.get('height') or size
                vals['pos_x'], vals['pos_y'] = floor._free_spot(size)
        return super().create(vals_list)

    @api.constrains('width', 'height', 'seats')
    def _check_dimensions(self):
        for table in self:
            if table.width < 30 or table.height < 30:
                raise UserError(_("Une table doit mesurer au moins 30 × 30 sur le plan."))
            if table.seats < 1:
                raise UserError(_("Une table doit avoir au moins une place."))

    # ------------------------------------------------------------------
    # Calculs d'état (non stockés : ils dépendent de l'heure et des commandes en cours)
    # ------------------------------------------------------------------
    def _open_orders_map(self):
        orders = self.env['adk.pos.order'].sudo().search([
            ('table_id', 'in', self.ids), ('state', 'in', ('draft', 'sent', 'billing')),
        ], order='create_date desc')
        mapping = {}
        for order in orders:
            mapping.setdefault(order.table_id.id, self.env['adk.pos.order'])
            mapping[order.table_id.id] |= order
        return mapping

    def _upcoming_reservations_map(self, now=None):
        now = now or fields.Datetime.now()
        if not self.ids:
            return {}
        max_lead = max(self.mapped('config_id.reservation_lead_minutes') or [0])
        reservations = self.env['adk.pos.reservation'].sudo().search([
            ('state', '=', 'confirmed'),
            ('table_ids', 'in', self.ids),
            ('date_start', '<=', now + timedelta(minutes=max_lead)),
            ('date_end', '>', now),
        ], order='date_start')
        mapping = {}
        for res in reservations:
            for table in res.table_ids & self:
                lead = table.config_id.reservation_lead_minutes
                if res.date_start <= now + timedelta(minutes=lead):
                    mapping.setdefault(table.id, res)
        return mapping

    def _compute_current_order(self):
        orders_map = self._open_orders_map()
        for table in self:
            orders = orders_map.get(table.id)
            table.current_order_id = orders[:1].id if orders else False

    def _compute_state(self):
        orders_map = self._open_orders_map()
        reservations_map = self._upcoming_reservations_map()
        for table in self:
            orders = orders_map.get(table.id)
            if orders:
                table.state = 'billing' if any(o.state == 'billing' for o in orders) else 'occupied'
            elif table.is_reserved or table.id in reservations_map:
                table.state = 'reserved'
            else:
                table.state = 'free'

    # Anciennes actions (compatibilité)
    def action_reserve(self, note=''):
        self.ensure_one()
        self.write({'is_reserved': True, 'reservation_note': note or ''})

    def action_cancel_reservation(self):
        self.ensure_one()
        self.write({'is_reserved': False, 'reservation_note': ''})

    # ------------------------------------------------------------------
    # API du terminal
    # ------------------------------------------------------------------
    @api.model
    def _terminal_employee(self, employee_id, role='server'):
        return self.env['adk.pos.order']._terminal_employee(employee_id, role)

    @api.model
    def get_floor_data(self, config_id):
        """Plan de salle complet, avec l'état en direct de chaque table (un seul appel)."""
        config = self.env['adk.pos.config'].browse(config_id).exists()
        if not config:
            return {'floors': [], 'tables': []}
        now = fields.Datetime.now()
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        tables = self.sudo().search([('config_id', '=', config.id)])
        orders_map = tables._open_orders_map()
        reservations_map = tables._upcoming_reservations_map(now)
        ready_items = self.env['adk.pos.kitchen.item'].sudo().search([
            ('state', '=', 'ready'), ('order_id.table_id', 'in', tables.ids),
            ('order_id.state', 'in', ('draft', 'sent', 'billing')),
        ])
        ready_by_table = {}
        for item in ready_items:
            ready_by_table[item.order_id.table_id.id] = ready_by_table.get(item.order_id.table_id.id, 0) + 1

        result = []
        for table in tables:
            orders = orders_map.get(table.id)
            res = reservations_map.get(table.id)
            state = 'free'
            if orders:
                state = 'billing' if any(o.state == 'billing' for o in orders) else 'occupied'
            elif res or table.is_reserved:
                state = 'reserved'
            since = min(orders.mapped('date_order')) if orders else False
            label = ''
            if res:
                local = pytz.utc.localize(res.date_start).astimezone(tz)
                label = "%s · %s (%s)" % (local.strftime('%H:%M'), res.customer_name or res.partner_id.name or '',
                                           res.guests)
            elif table.is_reserved:
                label = table.reservation_note or ''
            result.append({
                'id': table.id,
                'name': table.name,
                'floor_id': table.floor_id.id,
                'seats': table.seats,
                'shape': table.shape,
                'x': table.pos_x, 'y': table.pos_y, 'w': table.width, 'h': table.height,
                'state': state,
                'order_ids': orders.ids if orders else [],
                'order_count': len(orders) if orders else 0,
                'total': sum(orders.mapped('amount_total')) if orders else 0.0,
                'guests': sum(orders.mapped('guests')) if orders else 0,
                'elapsed_min': int((now - since).total_seconds() // 60) if since else 0,
                'server': orders[:1].server_id.name if orders else '',
                'reservation_id': res.id if res else False,
                'reservation_label': label,
                'is_reserved': table.is_reserved,
                'ready_count': ready_by_table.get(table.id, 0),
            })
        floors = [{
            'id': f.id, 'name': f.name, 'color': f.background_color,
            'width': f.canvas_width, 'height': f.canvas_height,
            'bg': ('/web/image/adk.pos.floor/%d/background_image?unique=%s' % (
                f.id, fields.Datetime.to_string(f.write_date) or '')) if f.sudo().background_image else '',
        } for f in config.floor_ids.filtered('active').sorted('sequence')]
        return {'floors': floors, 'tables': result}

    @api.model
    def terminal_save_layout(self, employee_id, changes):
        """Enregistre les positions / dimensions modifiées en mode édition du plan (Manager)."""
        self._terminal_employee(employee_id, 'manager')
        allowed = {'name', 'seats', 'shape', 'pos_x', 'pos_y', 'width', 'height'}
        for change in changes:
            table = self.browse(change.get('id')).exists()
            if not table:
                continue
            vals = {k: v for k, v in change.items() if k in allowed}
            if vals:
                table.sudo().write(vals)
        return True

    @api.model
    def terminal_add_table(self, employee_id, floor_id, vals=None):
        self._terminal_employee(employee_id, 'manager')
        floor = self.env['adk.pos.floor'].browse(floor_id).exists()
        if not floor:
            raise UserError(_("Salle introuvable."))
        vals = vals or {}
        count = len(floor.table_ids) + 1
        table = self.sudo().create({
            'name': vals.get('name') or "T%s" % count,
            'floor_id': floor.id,
            'seats': vals.get('seats') or 4,
            'shape': vals.get('shape') or 'square',
            'pos_x': vals.get('pos_x', 40 + (count % 6) * 20),
            'pos_y': vals.get('pos_y', 40 + (count % 6) * 20),
            'width': vals.get('width') or 100,
            'height': vals.get('height') or 100,
        })
        return table.id

    @api.model
    def terminal_remove_table(self, employee_id, table_id):
        self._terminal_employee(employee_id, 'manager')
        table = self.browse(table_id).exists()
        if not table:
            return True
        if self.env['adk.pos.order'].sudo().search_count([
                ('table_id', '=', table.id), ('state', 'in', ('draft', 'sent', 'billing'))]):
            raise UserError(_("Cette table a une commande en cours : impossible de la supprimer."))
        table.sudo().write({'active': False})
        return True

    @api.model
    def terminal_generate_tables(self, employee_id, floor_id, count, seats=4, shape='square',
                                 prefix='T', replace=False):
        self._terminal_employee(employee_id, 'manager')
        floor = self.env['adk.pos.floor'].sudo().browse(floor_id).exists()
        if not floor:
            raise UserError(_("Salle introuvable."))
        floor.generate_tables(count, seats, shape, prefix, replace)
        return True

    @api.model
    def terminal_arrange_tables(self, employee_id, floor_id):
        self._terminal_employee(employee_id, 'manager')
        self.env['adk.pos.floor'].sudo().browse(floor_id).exists().action_arrange_tables()
        return True

    @api.model
    def terminal_set_background(self, employee_id, floor_id, image_b64=False):
        self._terminal_employee(employee_id, 'manager')
        floor = self.env['adk.pos.floor'].sudo().browse(floor_id).exists()
        if not floor:
            raise UserError(_("Salle introuvable."))
        floor.write({'background_image': image_b64 or False})
        return True

    @api.model
    def terminal_add_floor(self, employee_id, config_id, name):
        self._terminal_employee(employee_id, 'manager')
        floor = self.env['adk.pos.floor'].sudo().create({
            'name': (name or '').strip() or _("Salle"), 'config_id': config_id})
        return floor.id
