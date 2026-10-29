# -*- coding: utf-8 -*-
import json
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AdkPosPaymentMethod(models.Model):
    _name = 'adk.pos.payment.method'
    _description = "Mode de paiement Point de Vente"
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    icon = fields.Selection([
        ('money', 'Espèces (billet)'),
        ('credit-card', 'Carte'),
        ('mobile', 'Mobile'),
        ('bank', 'Banque'),
        ('ellipsis-h', 'Autre'),
    ], string="Icône", default='money')
    is_cash = fields.Boolean(
        string="Paiement en espèces",
        help="Si coché, l'écran de caisse demande le montant reçu, calcule la monnaie à rendre "
             "et ce paiement est compté dans le tiroir-caisse.")
    requires_reference = fields.Boolean(
        string="Référence obligatoire",
        help="Si coché, l'écran de caisse exige une référence / pièce justificative "
             "(reçu de terminal, n° de transaction Mobile Money, etc.) avant de valider.")

    is_credit = fields.Boolean(
        string="Paiement à crédit",
        help="Le client n'a pas réglé : la commande est encaissée en caisse mais la facture reste "
             "NON PAYÉE jusqu'à l'enregistrement du règlement en comptabilité. Un client est obligatoire.")
    journal_id = fields.Many2one(
        'account.journal', string="Journal comptable",
        domain="[('type', 'in', ('bank', 'cash'))]",
        help="Journal utilisé pour enregistrer le paiement de la facture. "
             "Vide : le journal Espèces / Banque par défaut est utilisé.")

    terminal_type = fields.Selection([
        ('manual', "Saisie manuelle de la référence"),
        ('http', "Terminal de paiement / API (HTTP)"),
    ], string="Terminal de paiement", default='manual', required=True,
        help="« Terminal / API » envoie le montant à votre TPE, Mobile Money ou passerelle de paiement "
             "via une requête HTTP configurable, puis récupère automatiquement la référence.")
    terminal_url = fields.Char(string="URL du terminal / de l'API")
    terminal_method = fields.Selection([('POST', 'POST'), ('PUT', 'PUT'), ('GET', 'GET')], default='POST', string="Méthode")
    terminal_headers = fields.Text(
        string="En-têtes (JSON)", default='{"Content-Type": "application/json"}',
        help='Ex : {"Authorization": "Bearer MA_CLE", "Content-Type": "application/json"}')
    terminal_body = fields.Text(
        string="Corps de la requête (JSON)", default='{"amount": {amount}, "currency": "{currency}", "reference": "{order}"}',
        help="Variables disponibles : {amount} (montant), {amount_cents} (en centimes), {currency}, {order} "
             "(n° de commande), {phone} (téléphone du client).")
    terminal_ok_path = fields.Char(
        string="Champ « statut » de la réponse", default='status',
        help="Chemin dans la réponse JSON, ex : status ou data.state")
    terminal_ok_values = fields.Char(
        string="Valeurs de succès", default='approved,success,succeeded,paid,ok,completed',
        help="Liste séparée par des virgules. Le paiement est accepté si le statut vaut l'une d'elles.")
    terminal_ref_path = fields.Char(
        string="Champ « référence » de la réponse", default='transaction_id',
        help="Chemin dans la réponse JSON, ex : transaction_id ou data.receipt.number")
    terminal_timeout = fields.Integer(string="Délai d'attente (s)", default=60)

    @staticmethod
    def _dig(data, path):
        for key in (path or '').split('.'):
            if key == '':
                continue
            if isinstance(data, list):
                try:
                    data = data[int(key)]
                except (ValueError, IndexError):
                    return None
            elif isinstance(data, dict):
                data = data.get(key)
            else:
                return None
        return data

    @api.model
    def terminal_charge(self, method_id, amount, order_name, phone=''):
        """Envoie le montant au terminal / à l'API configuré(e) et renvoie le résultat."""
        if not self.env.user.has_group('adk_pos.group_adk_pos_cashier'):
            raise UserError(_("Réservé aux caissiers."))
        method = self.sudo().browse(method_id).exists()
        if not method or method.terminal_type != 'http' or not method.terminal_url:
            raise UserError(_("Ce mode de paiement n'est pas relié à un terminal."))
        if not method.terminal_url.lower().startswith(('http://', 'https://')):
            raise UserError(_("L'URL du terminal doit commencer par http:// ou https://"))
        currency = self.env.company.currency_id
        values = {'amount': ('%.*f' % (currency.decimal_places, amount)),
                  'amount_cents': str(int(round(amount * 100))),
                  'currency': currency.name, 'order': order_name or '', 'phone': phone or ''}
        body = method.terminal_body or ''
        for key, val in values.items():
            body = body.replace('{%s}' % key, val)
        try:
            headers = json.loads(method.terminal_headers or '{}')
            payload = json.loads(body) if body.strip() else None
        except ValueError as exc:
            raise UserError(_("Configuration du terminal invalide (JSON) : %s") % exc)
        try:
            resp = requests.request(method.terminal_method, method.terminal_url, headers=headers,
                                    json=payload if method.terminal_method != 'GET' else None,
                                    params=payload if method.terminal_method == 'GET' else None,
                                    timeout=method.terminal_timeout or 60)
        except requests.RequestException as exc:
            _logger.warning("Terminal de paiement injoignable : %s", exc)
            raise UserError(_("Terminal injoignable : %s") % exc)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        status = str(self._dig(data, method.terminal_ok_path) or '').lower()
        oks = [v.strip().lower() for v in (method.terminal_ok_values or '').split(',') if v.strip()]
        ok = resp.ok and (status in oks if method.terminal_ok_path else True)
        ref = self._dig(data, method.terminal_ref_path) if method.terminal_ref_path else ''
        return {'ok': bool(ok), 'reference': str(ref or ''), 'status': status,
                'message': '' if ok else _("Paiement refusé ou non confirmé par le terminal (statut : %s).") % (status or resp.status_code)}
