# -*- coding: utf-8 -*-
"""Facturation client : une commande payée en caisse génère une facture client Odoo.

* Client renseigné            -> facture à son nom.
* Pas de client (mode « toujours ») -> facture au « Client divers » du point de vente,
  modifiable plus tard (même session fermée).
* Paiement « À crédit »       -> la facture reste NON PAYÉE (règlement à enregistrer en comptabilité).
* Autres modes                -> un paiement comptable est enregistré et lettré avec la facture.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

LOCKED_ORDER_FIELDS = {'table_id', 'guests', 'server_id', 'order_type', 'session_id', 'config_id',
                       'date_order', 'cashier_id', 'delivery_address'}


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    adk_pos_order_id = fields.Many2one('adk.pos.order', string="Commande Point de Vente",
                                       copy=False, index=True, ondelete='set null')


class AdkPosOrderAccounting(models.Model):
    _inherit = 'adk.pos.order'

    invoice_id = fields.Many2one('account.move', string="Facture", copy=False, readonly=True,
                                 ondelete='set null')
    invoice_payment_state = fields.Selection(related='invoice_id.payment_state',
                                             string="Paiement de la facture")
    invoice_error = fields.Char(string="Erreur de facturation", copy=False, readonly=True)
    credit_note_id = fields.Many2one('account.move', string="Avoir", copy=False, readonly=True,
                                     ondelete='set null')
    account_payment_ids = fields.One2many('account.payment', 'adk_pos_order_id', string="Paiements comptables")

    # ------------------------------------------------------------------
    # Verrouillage après validation
    # ------------------------------------------------------------------
    def write(self, vals):
        if not self.env.context.get('adk_force'):
            locked = LOCKED_ORDER_FIELDS & set(vals)
            if locked:
                for order in self:
                    if order.state in ('paid', 'cancelled'):
                        raise UserError(_(
                            "La commande %s est validée : la table, le nombre de couverts, le serveur, "
                            "la session et le type de commande ne sont plus modifiables. "
                            "Seul le client peut encore être renseigné.") % order.name)
        old_partners = {o.id: o.partner_id.id for o in self}
        res = super().write(vals)
        if 'partner_id' in vals:
            for order in self:
                if order.state == 'paid' and old_partners.get(order.id) != order.partner_id.id:
                    order._on_partner_changed()
        return res

    # ------------------------------------------------------------------
    # Facture
    # ------------------------------------------------------------------
    def _invoice_partner(self):
        self.ensure_one()
        if self.partner_id:
            return self.partner_id
        config = self.config_id
        if config.invoice_mode == 'always':
            return config.walkin_partner_id or self.env.ref(
                'adk_pos.partner_walkin', raise_if_not_found=False) or self.env['res.partner']
        return self.env['res.partner']

    def _invoice_journal(self):
        self.ensure_one()
        if self.config_id.invoice_journal_id:
            return self.config_id.invoice_journal_id
        return self.env['account.journal'].search([
            ('type', '=', 'sale'), ('company_id', '=', self.company_id.id)], limit=1)

    def _payment_journal(self, payment):
        method = payment.payment_method_id
        if method.journal_id:
            return method.journal_id
        Journal = self.env['account.journal']
        company = self.company_id.id
        wanted = ('cash', 'bank') if payment.is_cash else ('bank', 'cash')
        for jtype in wanted:
            journal = Journal.search([('type', '=', jtype), ('company_id', '=', company)], limit=1)
            if journal:
                return journal
        return Journal

    def _invoice_lines_vals(self, sign=1):
        self.ensure_one()
        vals = []
        for line in self.order_line_ids:
            qty = abs(line.qty)
            if not qty:
                continue
            vals.append((0, 0, {
                'product_id': line.product_id.id,
                'name': line.name or line.product_id.display_name,
                'quantity': qty,
                'price_unit': line.price_unit,
                'discount': line.discount,
                'tax_ids': [(6, 0, line.tax_ids.ids)],
            }))
        return vals

    def _generate_invoice(self):
        """Crée, valide et règle la facture d'une commande payée (sans jamais bloquer l'encaissement)."""
        for order in self.sudo().filtered(lambda o: o.state == 'paid'):
            if order.config_id.invoice_mode == 'off' or order.invoice_id or order.credit_note_id:
                continue
            partner = order._invoice_partner()
            if not partner:
                continue
            try:
                with self.env.cr.savepoint():
                    if order.is_refund:
                        order._generate_credit_note(partner)
                    else:
                        order._generate_customer_invoice(partner)
                order.invoice_error = False
            except Exception as exc:  # la caisse ne doit jamais être bloquée par la comptabilité
                _logger.warning("ADK POS : facturation de %s impossible : %s", order.name, exc)
                msg = getattr(exc, 'name', None) or str(exc)
                order.invoice_error = (msg or '')[:250]
                order.message_post(body=_("Facture non générée : %s") % msg)

    def _generate_customer_invoice(self, partner):
        self.ensure_one()
        date = (self.date_paid or self.date_order or fields.Datetime.now()).date()
        vals = {
            'move_type': 'out_invoice', 'partner_id': partner.id, 'invoice_date': date,
            'invoice_origin': self.name, 'ref': self.name, 'company_id': self.company_id.id,
            'currency_id': self.currency_id.id, 'invoice_line_ids': self._invoice_lines_vals(),
            'invoice_user_id': self.server_id.id or False,
        }
        journal = self._invoice_journal()
        if journal:
            vals['journal_id'] = journal.id
        invoice = self.env['account.move'].with_company(self.company_id).create(vals)
        invoice.action_post()
        self.invoice_id = invoice
        self._register_invoice_payments(invoice)
        self.message_post(body=_("Facture %s générée.") % invoice.name)
        return invoice

    def _register_invoice_payments(self, invoice, direction='in'):
        """Un paiement comptable par paiement de caisse (sauf « À crédit »), lettré avec la facture."""
        self.ensure_one()
        Register = self.env['account.payment.register']
        for pay in self.payment_ids.sorted('id'):
            method = pay.payment_method_id
            amount = abs(pay.amount)
            if method.is_credit or amount <= 0:
                continue
            residual = abs(invoice.amount_residual)
            if invoice.currency_id.is_zero(residual):
                break
            amount = min(amount, residual)
            journal = self._payment_journal(pay)
            if not journal:
                raise UserError(_("Aucun journal Espèces / Banque trouvé pour enregistrer le paiement."))
            wizard = Register.with_context(active_model='account.move', active_ids=invoice.ids).create({
                'amount': amount, 'journal_id': journal.id,
                'payment_date': (pay.date or fields.Datetime.now()).date(),
                'communication': "%s - %s" % (self.name, method.name),
            })
            payments = wizard._create_payments()
            payments.write({'adk_pos_order_id': self.id})

    def _generate_credit_note(self, partner):
        self.ensure_one()
        origin = self.refund_of_id
        date = (self.date_paid or fields.Datetime.now()).date()
        vals = {
            'move_type': 'out_refund', 'partner_id': partner.id, 'invoice_date': date,
            'invoice_origin': self.name, 'ref': self.name, 'company_id': self.company_id.id,
            'currency_id': self.currency_id.id, 'invoice_line_ids': self._invoice_lines_vals(),
        }
        if origin.invoice_id:
            vals['reversed_entry_id'] = origin.invoice_id.id
        journal = self._invoice_journal()
        if journal:
            vals['journal_id'] = journal.id
        note = self.env['account.move'].with_company(self.company_id).create(vals)
        note.action_post()
        self.credit_note_id = note
        self._register_invoice_payments(note)
        self.message_post(body=_("Avoir %s généré.") % note.name)

    # ------------------------------------------------------------------
    # Changement de client après encaissement (session fermée comprise)
    # ------------------------------------------------------------------
    def _on_partner_changed(self):
        for order in self.sudo():
            try:
                with self.env.cr.savepoint():
                    if not order.invoice_id:
                        order._generate_invoice()
                    elif order.partner_id and order.invoice_id.partner_id != order.partner_id:
                        order._reassign_invoice_partner(order.partner_id)
            except Exception as exc:
                _logger.warning("ADK POS : changement de client de %s : %s", order.name, exc)
                order.message_post(body=_("Le client de la facture n'a pas pu être modifié : %s") % exc)

    def _reassign_invoice_partner(self, partner):
        self.ensure_one()
        invoice = self.invoice_id
        payments = self.account_payment_ids.filtered(lambda p: p.state in ('in_process', 'paid'))
        for payment in payments:
            payment.action_draft()
        invoice.button_draft()
        invoice.write({'partner_id': partner.id})
        invoice.action_post()
        for payment in payments:
            payment._write({'partner_id': partner.id})  # sans resynchroniser les écritures
            payment.move_id.line_ids.write({'partner_id': partner.id})
            payment.move_id.write({'partner_id': partner.id})
            payment.action_post()
            lines = payment.move_id.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)
            for line in lines:
                invoice.js_assign_outstanding_line(line.id)
        self.message_post(body=_("Facture %s rattachée à %s.") % (invoice.name, partner.name))

    def action_generate_invoice(self):
        for order in self:
            if order.state != 'paid':
                raise UserError(_("Seule une commande payée peut être facturée."))
            if not order._invoice_partner():
                raise UserError(_("Renseignez d'abord le client de la commande."))
            order._generate_invoice()
            if order.invoice_error:
                raise UserError(order.invoice_error)
        return True

    def action_view_invoice(self):
        self.ensure_one()
        move = self.invoice_id or self.credit_note_id
        if not move:
            raise UserError(_("Aucune facture pour cette commande."))
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': move.id,
                'view_mode': 'form', 'target': 'current'}

    def _finalize_payment(self, employee=None):
        res = super()._finalize_payment(employee)
        self.filtered(lambda o: o.state == 'paid')._generate_invoice()
        return res

    # Exposé au terminal
    def _terminal_dict(self):
        res = super()._terminal_dict()
        move = self.sudo().invoice_id or self.sudo().credit_note_id
        res.update({
            'invoice': move.name if move else '',
            'invoice_state': move.payment_state if move else '',
            'invoice_error': self.invoice_error or '',
            'partner_is_walkin': bool(not self.partner_id),
        })
        return res
