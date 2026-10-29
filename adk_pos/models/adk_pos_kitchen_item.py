# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .adk_pos_order_line import COURSES

COURSE_RANK = {'drink': 0, 'starter': 1, 'main': 2, 'dessert': 3, 'other': 4}


class AdkPosKitchenItem(models.Model):
    _name = 'adk.pos.kitchen.item'
    _description = "Article envoyé en préparation"
    _order = 'date_sent, id'

    order_id = fields.Many2one('adk.pos.order', string="Commande", required=True,
                               ondelete='cascade', index=True)
    line_id = fields.Many2one('adk.pos.order.line', string="Ligne", ondelete='set null')
    config_id = fields.Many2one(related='order_id.config_id', store=True, index=True)
    table_id = fields.Many2one(related='order_id.table_id', store=True)
    server_id = fields.Many2one(related='order_id.server_id', store=True)
    product_id = fields.Many2one('product.product', string="Produit", required=True)
    name = fields.Char(string="Désignation", required=True)
    qty = fields.Float(string="Quantité", default=1.0)
    note = fields.Char(string="Note")
    course = fields.Selection(COURSES, string="Service", default='other')
    station_id = fields.Many2one('adk.pos.station', string="Poste de préparation")
    batch_no = fields.Integer(string="N° d'envoi", default=1)
    state = fields.Selection([
        ('new', 'Nouveau'),
        ('preparing', 'En préparation'),
        ('ready', 'Prêt'),
        ('served', 'Servi'),
        ('cancelled', 'Annulé'),
    ], default='new', index=True, string="Statut")
    received = fields.Boolean(string="Reçu par le poste", default=False, index=True,
                              help="Tant que le ticket n'est pas marqué « Reçu » (ou pris en préparation), "
                                   "l'écran de préparation sonne en continu.")
    date_received = fields.Datetime(string="Reçu le")
    acknowledged = fields.Boolean(string="Annulation vue par la cuisine", default=False)
    date_sent = fields.Datetime(default=fields.Datetime.now, string="Envoyé le")
    date_ready = fields.Datetime(string="Prêt le")
    date_served = fields.Datetime(string="Servi le")

    @api.model
    def get_board(self, config_id, station_id=False):
        """Écran cuisine : tickets regroupés par envoi (commande + n° d'envoi)."""
        now = fields.Datetime.now()
        domain = [
            ('config_id', '=', config_id),
            '|', ('state', 'in', ('new', 'preparing', 'ready')),
            '&', ('state', '=', 'cancelled'), ('acknowledged', '=', False),
        ]
        user = self.env.user
        Station = self.env['adk.pos.station'].sudo()
        config = self.env['adk.pos.config'].sudo().browse(config_id)
        all_stations = Station.search([('config_ids', 'in', config_id)], order='sequence, id')
        allowed_ids = set(all_stations.ids)
        if not user.has_group('adk_pos.group_adk_pos_manager'):
            allowed_ids = {s['id'] for s in Station.get_stations_for_user(config_id)}
            if station_id and station_id not in allowed_ids:
                raise AccessError(_("Vous n'avez pas accès à cet écran de préparation."))
            if all_stations and not allowed_ids:
                return []
        default_station = all_stations[:1]
        items = self.sudo().search(domain, order='date_sent, id', limit=800)
        tickets = {}
        for item in items:
            # Poste réel de l'article : celui enregistré, sinon celui de sa catégorie,
            # sinon le premier poste (jamais « partout à la fois »).
            station = item.station_id
            if not station or station not in all_stations:
                station = Station._get_for_product(config, item.product_id) or default_station
            if all_stations and station.id not in allowed_ids:
                continue
            if station_id and station.id != station_id:
                continue
            key = (item.order_id.id, item.batch_no, station.id)
            ticket = tickets.get(key)
            if not ticket:
                order = item.order_id
                ticket = tickets[key] = {
                    'key': "%s-%s-%s" % key,
                    'order_id': order.id,
                    'order_name': order.name,
                    'batch_no': item.batch_no,
                    'station_id': station.id,
                    'station_name': station.name or '',
                    'station_color': (station.color or 0) % 12,
                    'station_seq': station.sequence,
                    'table': order.table_id.name or '',
                    'order_type': dict(order._fields['order_type'].selection).get(order.order_type, ''),
                    'server': order.server_id.name or '',
                    'guests': order.guests,
                    'note': order.note or '',
                    'sent_min': int((now - item.date_sent).total_seconds() // 60),
                    'items': [],
                }
            ticket['items'].append({
                'id': item.id, 'name': item.name, 'qty': item.qty, 'note': item.note or '',
                'course': item.course, 'state': item.state, 'received': item.received,
                'course_rank': COURSE_RANK.get(item.course, 4),
                'categ': item.product_id.categ_id.name or '',
            })
        result = list(tickets.values())
        for ticket in result:
            ticket['items'].sort(key=lambda i: (i['course_rank'], i['id']))
            states = {i['state'] for i in ticket['items'] if i['state'] != 'cancelled'}
            ticket['received'] = all(i['received'] for i in ticket['items'] if i['state'] != 'cancelled')
            if not states:
                ticket['state'] = 'cancelled'
            elif states == {'ready'}:
                ticket['state'] = 'ready'
            elif 'preparing' in states or 'ready' in states:
                ticket['state'] = 'preparing'
            else:
                ticket['state'] = 'new'
        result.sort(key=lambda t: (t['station_seq'], t['station_id'], -t['sent_min']))
        return result

    @api.model
    def set_state(self, ids, state):
        if state not in ('new', 'preparing', 'ready', 'served'):
            raise UserError(_("Statut invalide."))
        items = self.sudo().browse(ids).exists().filtered(lambda i: i.state != 'cancelled')
        vals = {'state': state}
        now = fields.Datetime.now()
        # Prendre un article en charge vaut accusé de réception : l'alarme s'arrête
        unreceived = items.filtered(lambda i: not i.received)
        if unreceived and state != 'new':
            unreceived.write({'received': True, 'date_received': now})
        if state == 'ready':
            vals['date_ready'] = now
        elif state == 'served':
            vals['date_served'] = now
        items.write(vals)
        return True

    @api.model
    def mark_received(self, ids):
        """Bouton « Reçu » : le poste a vu le ticket, l'alarme sonore s'arrête."""
        items = self.sudo().browse(ids).exists().filtered(lambda i: not i.received and i.state != 'cancelled')
        items.write({'received': True, 'date_received': fields.Datetime.now()})
        return True

    @api.model
    def acknowledge_cancel(self, ids):
        self.sudo().browse(ids).exists().filtered(lambda i: i.state == 'cancelled').write(
            {'acknowledged': True})
        return True
