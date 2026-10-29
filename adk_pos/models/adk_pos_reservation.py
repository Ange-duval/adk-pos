# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

ACTIVE_STATES = ('confirmed', 'seated')


class AdkPosReservation(models.Model):
    _name = 'adk.pos.reservation'
    _description = "Réservation de table"
    _inherit = ['mail.thread']
    _order = 'date_start desc, id desc'

    name = fields.Char(string="Référence", required=True, copy=False, readonly=True, default='/')
    config_id = fields.Many2one('adk.pos.config', string="Point de vente", required=True,
                                ondelete='cascade', tracking=True)
    company_id = fields.Many2one(related='config_id.company_id', store=True)
    partner_id = fields.Many2one('res.partner', string="Client")
    customer_name = fields.Char(string="Nom du client", tracking=True)
    phone = fields.Char(string="Téléphone")
    guests = fields.Integer(string="Nombre de personnes", default=2, tracking=True)
    date_start = fields.Datetime(string="Arrivée prévue", required=True, tracking=True,
                                 default=lambda self: fields.Datetime.now() + timedelta(hours=1))
    duration = fields.Integer(string="Durée (min)", default=90)
    date_end = fields.Datetime(string="Fin prévue", compute='_compute_date_end', store=True)
    table_ids = fields.Many2many('adk.pos.table', 'adk_pos_reservation_table_rel',
                                 'reservation_id', 'table_id', string="Table(s)",
                                 domain="[('config_id', '=', config_id)]")
    state = fields.Selection([
        ('confirmed', 'Confirmée'),
        ('seated', 'Installée'),
        ('done', 'Terminée'),
        ('no_show', 'Non présenté'),
        ('cancelled', 'Annulée'),
    ], default='confirmed', tracking=True, string="Statut", index=True)
    source = fields.Selection([
        ('phone', 'Téléphone'), ('walkin', 'Sur place'), ('web', 'En ligne'), ('other', 'Autre'),
    ], string="Origine", default='phone')
    note = fields.Text(string="Notes (anniversaire, allergies, étage…)")
    order_id = fields.Many2one('adk.pos.order', string="Commande", copy=False, ondelete='set null')

    @api.depends('date_start', 'duration')
    def _compute_date_end(self):
        for res in self:
            res.date_end = (res.date_start + timedelta(minutes=res.duration or 90)) if res.date_start else False

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            self.customer_name = self.customer_name or self.partner_id.name
            self.phone = self.phone or self.partner_id.phone

    @api.onchange('config_id')
    def _onchange_config_id(self):
        if self.config_id:
            self.duration = self.config_id.reservation_duration or 90

    @api.constrains('guests', 'duration', 'table_ids')
    def _check_values(self):
        for res in self:
            if res.guests < 1:
                raise ValidationError(_("Le nombre de personnes doit être d'au moins 1."))
            if res.duration < 15:
                raise ValidationError(_("La durée d'une réservation doit être d'au moins 15 minutes."))
            if res.table_ids.filtered(lambda t: t.config_id != res.config_id):
                raise ValidationError(_("Toutes les tables doivent appartenir au point de vente de la réservation."))

    @api.constrains('table_ids', 'date_start', 'duration', 'state')
    def _check_overlap(self):
        for res in self:
            if res.state not in ACTIVE_STATES or not res.table_ids:
                continue
            other = self.search([
                ('id', '!=', res.id), ('state', 'in', ACTIVE_STATES),
                ('table_ids', 'in', res.table_ids.ids),
                ('date_start', '<', res.date_end), ('date_end', '>', res.date_start),
            ], limit=1)
            if other:
                shared = (other.table_ids & res.table_ids).mapped('name')
                raise ValidationError(_(
                    "La table %(tables)s est déjà réservée sur ce créneau (%(ref)s – %(customer)s).",
                    tables=", ".join(shared), ref=other.name, customer=other.customer_name or ''))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code('adk.pos.reservation') or '/'
            if not vals.get('duration') and vals.get('config_id'):
                vals['duration'] = self.env['adk.pos.config'].browse(vals['config_id']).reservation_duration or 90
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_cancel(self):
        self.filtered(lambda r: r.state in ACTIVE_STATES).write({'state': 'cancelled'})

    def action_no_show(self):
        self.filtered(lambda r: r.state == 'confirmed').write({'state': 'no_show'})

    def action_done(self):
        self.filtered(lambda r: r.state in ACTIVE_STATES).write({'state': 'done'})

    @api.model
    def _cron_mark_no_show(self):
        """Marque « Non présenté » les réservations dépassées sans client installé."""
        now = fields.Datetime.now()
        for config in self.env['adk.pos.config'].search([]):
            limit = now - timedelta(minutes=config.reservation_grace_minutes or 30)
            self.search([('config_id', '=', config.id), ('state', '=', 'confirmed'),
                         ('date_start', '<', limit)]).write({'state': 'no_show'})

    # ------------------------------------------------------------------
    # API du terminal
    # ------------------------------------------------------------------
    @api.model
    def _local_to_utc(self, date_str, time_str):
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        try:
            naive = datetime.strptime("%s %s" % (date_str, time_str), '%Y-%m-%d %H:%M')
        except (TypeError, ValueError):
            raise UserError(_("Date ou heure invalide."))
        return tz.localize(naive).astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def terminal_list(self, config_id):
        """Réservations du jour et des 7 prochains jours."""
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        now = fields.Datetime.now()
        today_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today_local.astimezone(pytz.utc).replace(tzinfo=None)
        records = self.search([
            ('config_id', '=', config_id), ('state', 'in', ACTIVE_STATES),
            ('date_start', '>=', start), ('date_start', '<', start + timedelta(days=8)),
        ], order='date_start')
        result = []
        for res in records:
            local = pytz.utc.localize(res.date_start).astimezone(tz)
            result.append({
                'id': res.id, 'name': res.name,
                'customer': res.customer_name or res.partner_id.name or _("Sans nom"),
                'phone': res.phone or '', 'guests': res.guests, 'state': res.state,
                'time': local.strftime('%H:%M'), 'date': local.strftime('%Y-%m-%d'),
                'day_label': local.strftime('%d/%m'),
                'is_today': local.date() == today_local.date(),
                'minutes_until': int((res.date_start - now).total_seconds() // 60),
                'tables': ", ".join(res.table_ids.mapped('name')),
                'table_ids': res.table_ids.ids, 'note': res.note or '',
                'order_id': res.order_id.id or False,
            })
        return result

    @api.model
    def terminal_available_tables(self, config_id, date_str, time_str, duration=90, exclude_id=False):
        """Tables libres sur un créneau (ni réservées ni déjà occupées si le créneau est imminent)."""
        start = self._local_to_utc(date_str, time_str)
        end = start + timedelta(minutes=int(duration or 90))
        domain = [('config_id', '=', config_id), ('state', 'in', ACTIVE_STATES),
                  ('date_start', '<', end), ('date_end', '>', start)]
        if exclude_id:
            domain.append(('id', '!=', exclude_id))
        busy = self.search(domain).mapped('table_ids').ids
        now = fields.Datetime.now()
        occupied = []
        if start <= now + timedelta(hours=1):
            occupied = self.env['adk.pos.order'].sudo().search([
                ('config_id', '=', config_id), ('state', 'in', ('draft', 'sent', 'billing')),
                ('table_id', '!=', False)]).mapped('table_id').ids
        tables = self.env['adk.pos.table'].search([('config_id', '=', config_id)])
        return [{
            'id': t.id, 'name': t.name, 'seats': t.seats, 'floor': t.floor_id.name,
            'available': t.id not in busy and t.id not in occupied,
            'reason': ('busy' if t.id in busy else ('occupied' if t.id in occupied else '')),
        } for t in tables]

    @api.model
    def terminal_create(self, config_id, vals):
        config = self.env['adk.pos.config'].browse(config_id).exists()
        if not config:
            raise UserError(_("Point de vente introuvable."))
        customer = (vals.get('customer_name') or '').strip()
        if not customer:
            raise UserError(_("Indiquez le nom du client."))
        start = self._local_to_utc(vals.get('date'), vals.get('time'))
        if start < fields.Datetime.now() - timedelta(minutes=30):
            raise UserError(_("Cette heure est déjà passée."))
        res = self.create({
            'config_id': config.id, 'customer_name': customer,
            'phone': (vals.get('phone') or '').strip() or False,
            'guests': max(int(vals.get('guests') or 2), 1),
            'date_start': start,
            'duration': int(vals.get('duration') or config.reservation_duration or 90),
            'table_ids': [(6, 0, vals.get('table_ids') or [])],
            'note': vals.get('note') or False, 'source': vals.get('source') or 'phone',
            'partner_id': vals.get('partner_id') or False,
        })
        return res.id

    @api.model
    def terminal_set_state(self, reservation_id, state):
        res = self.browse(reservation_id).exists()
        if not res:
            return True
        if state == 'cancelled':
            res.action_cancel()
        elif state == 'no_show':
            res.action_no_show()
        else:
            raise UserError(_("Action non autorisée."))
        return True
