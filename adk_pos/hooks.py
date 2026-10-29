# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def post_init_hook(env_or_cr, registry=None):
    """Après l'installation : l'administrateur devient manager du point de vente
    (accès aux Paramètres, à l'assistant de démarrage et à tous les écrans).

    Odoo 18 passe un Environment ; les anciennes versions passaient (cr, registry)."""
    env = env_or_cr if isinstance(env_or_cr, api.Environment) else api.Environment(env_or_cr, SUPERUSER_ID, {})
    manager = env.ref('adk_pos.group_adk_pos_manager', raise_if_not_found=False)
    if not manager:
        return
    users = env['res.users'].browse(SUPERUSER_ID)
    admin = env.ref('base.user_admin', raise_if_not_found=False)
    if admin:
        users |= admin
    users.write({'groups_id': [(4, manager.id)]})
