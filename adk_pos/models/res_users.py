# -*- coding: utf-8 -*-
from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

MAX_PIN_ATTEMPTS = 5
PIN_LOCK_MINUTES = 5


class ResUsers(models.Model):
    _inherit = 'res.users'

    adk_pos_pin_hash = fields.Char(string="Code PIN Point de Vente (haché)", copy=False,
                                   groups='adk_pos.group_adk_pos_manager')
    adk_pos_pin_failed = fields.Integer(string="Échecs PIN consécutifs", copy=False,
                                        groups='adk_pos.group_adk_pos_manager')
    adk_pos_pin_locked_until = fields.Datetime(string="PIN verrouillé jusqu'à", copy=False,
                                               groups='adk_pos.group_adk_pos_manager')
    adk_pos_pin_input = fields.Char(
        string="Code PIN Point de Vente", copy=False, store=False,
        compute='_compute_adk_pos_pin_input', inverse='_inverse_adk_pos_pin_input',
        help="Code à 4–6 chiffres utilisé pour se connecter au terminal ADK Point de Vente. "
             "Laissez vide pour ne pas le modifier. Il est stocké haché (illisible).")
    adk_pos_role = fields.Selection([
        ('server', 'Serveur'), ('cashier', 'Caissier'), ('manager', 'Manager'),
    ], string="Rôle Point de Vente", compute='_compute_adk_pos_role_field',
        inverse='_inverse_adk_pos_role_field')
    adk_pos_station_ids = fields.Many2many(
        'adk.pos.station', 'adk_pos_station_user_rel', 'user_id', 'station_id',
        string="Écrans de préparation autorisés",
        help="Cuisine, Bar… visibles par cette personne. Vide = tous les écrans du point de vente.")
    adk_pos_config_ids = fields.Many2many(
        'adk.pos.config', string="Points de vente autorisés",
        compute='_compute_adk_pos_config_ids', inverse='_inverse_adk_pos_config_ids',
        help="Points de vente sur lesquels cette personne peut se connecter avec son code PIN.")
    adk_pos_has_pin = fields.Boolean(string="Code PIN défini", compute='_compute_adk_pos_has_pin')

    def _compute_adk_pos_has_pin(self):
        for user in self:
            user.adk_pos_has_pin = bool(user.sudo().adk_pos_pin_hash)

    def _compute_adk_pos_pin_input(self):
        for user in self:
            user.adk_pos_pin_input = False

    def _inverse_adk_pos_pin_input(self):
        for user in self:
            pin = (user.adk_pos_pin_input or '').strip()
            if not pin:
                continue
            if not pin.isdigit() or not (4 <= len(pin) <= 6):
                raise ValidationError(_("Le code PIN doit contenir entre 4 et 6 chiffres."))
            user.adk_pos_set_pin(pin)

    @api.depends('groups_id')
    def _compute_adk_pos_role_field(self):
        for user in self:
            user.adk_pos_role = user._adk_pos_role() or False

    def _inverse_adk_pos_role_field(self):
        groups = {
            'server': self.env.ref('adk_pos.group_adk_pos_server'),
            'cashier': self.env.ref('adk_pos.group_adk_pos_cashier'),
            'manager': self.env.ref('adk_pos.group_adk_pos_manager'),
        }
        for user in self.sudo():
            role = user.adk_pos_role
            for g in groups.values():
                if g in user.groups_id:
                    user.groups_id = [(3, g.id)]
            if role:
                user.groups_id = [(4, groups[role].id)]

    def _compute_adk_pos_config_ids(self):
        Config = self.env['adk.pos.config'].sudo()
        for user in self:
            user.adk_pos_config_ids = Config.search([
                '|', '|', ('server_ids', 'in', user.id), ('cashier_ids', 'in', user.id),
                ('manager_id', '=', user.id)])

    def _inverse_adk_pos_config_ids(self):
        Config = self.env['adk.pos.config'].sudo()
        for user in self:
            wanted = user.adk_pos_config_ids.sudo()
            role = user._adk_pos_role() or 'server'
            for config in Config.search([]):
                member = user in config.server_ids or user in config.cashier_ids
                if config in wanted and not member:
                    config.write({'cashier_ids' if role in ('cashier', 'manager') else 'server_ids': [(4, user.id)]})
                elif config not in wanted and member:
                    config.write({'server_ids': [(3, user.id)], 'cashier_ids': [(3, user.id)]})

    def adk_pos_set_pin(self, pin):
        """Définit (ou change) le code PIN. À appeler depuis un contexte contrôlé (assistant Manager)."""
        for user in self:
            user.sudo().write({
                'adk_pos_pin_hash': generate_password_hash(pin) if pin else False,
                'adk_pos_pin_failed': 0,
                'adk_pos_pin_locked_until': False,
            })

    def adk_pos_unlock_pin(self):
        self.sudo().write({'adk_pos_pin_failed': 0, 'adk_pos_pin_locked_until': False})

    def _adk_pos_role(self):
        """Rôle Point de Vente le plus élevé : manager > cashier > server."""
        self.ensure_one()
        user = self.sudo()
        if user.has_group('adk_pos.group_adk_pos_manager'):
            return 'manager'
        if user.has_group('adk_pos.group_adk_pos_cashier'):
            return 'cashier'
        if user.has_group('adk_pos.group_adk_pos_server'):
            return 'server'
        return False

    @api.model
    def adk_pos_check_pin(self, user_id, pin):
        """Vérifie le code PIN d'un collaborateur (utilisable depuis un compte de caisse partagé).

        Après 5 échecs consécutifs, le PIN est verrouillé 5 minutes pour empêcher
        de deviner un code par essais successifs.
        """
        user = self.sudo().browse(user_id).exists()
        if not user or not user.adk_pos_pin_hash or not pin:
            return {'ok': False, 'error': 'invalid'}
        now = fields.Datetime.now()
        if user.adk_pos_pin_locked_until and user.adk_pos_pin_locked_until > now:
            minutes = int((user.adk_pos_pin_locked_until - now).total_seconds() // 60) + 1
            return {'ok': False, 'error': 'locked', 'minutes': minutes}
        if check_password_hash(user.adk_pos_pin_hash, str(pin)):
            if user.adk_pos_pin_failed or user.adk_pos_pin_locked_until:
                user.write({'adk_pos_pin_failed': 0, 'adk_pos_pin_locked_until': False})
            return {'ok': True, 'id': user.id, 'name': user.name, 'role': user._adk_pos_role()}
        failed = user.adk_pos_pin_failed + 1
        vals = {'adk_pos_pin_failed': failed}
        if failed >= MAX_PIN_ATTEMPTS:
            vals = {'adk_pos_pin_failed': 0,
                    'adk_pos_pin_locked_until': now + timedelta(minutes=PIN_LOCK_MINUTES)}
        user.write(vals)
        return {'ok': False, 'error': 'invalid', 'remaining': max(MAX_PIN_ATTEMPTS - failed, 0)}

    @api.model
    def adk_pos_get_managers(self, config_id=False):
        """Liste des managers pouvant valider une opération sensible (remise, remboursement…)."""
        group = self.env.ref('adk_pos.group_adk_pos_manager')
        managers = self.sudo().search([('groups_id', 'in', group.id), ('share', '=', False)])
        managers = managers.filtered(lambda u: u.adk_pos_pin_hash)
        return [{'id': m.id, 'name': m.name} for m in managers]

    @api.model
    def adk_pos_approve(self, manager_id, pin):
        """Valide l'accord d'un manager (retourne son nom si le PIN est correct)."""
        res = self.adk_pos_check_pin(manager_id, pin)
        if res.get('ok') and res.get('role') == 'manager':
            return res
        if res.get('ok'):
            return {'ok': False, 'error': 'not_manager'}
        return res
