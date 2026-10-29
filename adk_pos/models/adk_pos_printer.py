# -*- coding: utf-8 -*-
import base64
import logging
import socket

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .escpos import CODEPAGES, EscPos

_logger = logging.getLogger(__name__)

CONNECTIONS = [
    ('browser', "Navigateur (toute imprimante, via le pilote Windows / Mac / Linux)"),
    ('network', "Réseau — IP:port (ESC/POS, port 9100)"),
    ('webusb', "USB direct (Chrome / Edge)"),
    ('webserial', "Série / Bluetooth (Chrome / Edge)"),
    ('bridge', "Passerelle locale (agent d'impression HTTP)"),
]


class AdkPosPrinter(models.Model):
    _name = 'adk.pos.printer'
    _description = "Imprimante Point de Vente"
    _order = 'sequence, name'

    name = fields.Char(string="Nom", required=True, help="Ex : Caisse, Cuisine, Bar, Pâtisserie…")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    connection = fields.Selection(CONNECTIONS, string="Mode de connexion", required=True, default='browser')
    ip_address = fields.Char(string="Adresse IP")
    port = fields.Integer(string="Port", default=9100)
    bridge_url = fields.Char(
        string="URL de la passerelle", default='http://localhost:9100/print',
        help="Adresse d'un petit agent installé sur le PC de caisse. Il reçoit un POST JSON "
             "{\"printer\": \"nom\", \"data\": \"<ESC/POS en base64>\"} et imprime. Compatible avec tout "
             "agent qui accepte ce format.")
    bridge_printer_name = fields.Char(string="Nom de l'imprimante côté passerelle")
    paper_width = fields.Selection([('32', "58 mm (32 caractères)"), ('42', "72 mm (42 caractères)"),
                                    ('48', "80 mm (48 caractères)")], string="Largeur du papier",
                                   default='48', required=True)
    codepage = fields.Selection([(k, k.upper()) for k in CODEPAGES], string="Jeu de caractères", default='cp858',
                                help="CP858 convient à la grande majorité des imprimantes pour le français. "
                                     "Si les accents s'affichent mal, essayez CP437, CP850 ou ASCII.")
    serial_baud = fields.Integer(string="Vitesse série (bauds)", default=9600,
                                 help="9600 pour la plupart des imprimantes ; 115200 pour certaines imprimantes USB.")
    cut_paper = fields.Boolean(string="Couper le papier", default=True)
    open_drawer = fields.Boolean(string="Ouvrir le tiroir-caisse", default=False,
                                 help="Envoie l'impulsion d'ouverture du tiroir branché sur cette imprimante "
                                      "lors d'un encaissement en espèces.")
    copies = fields.Integer(string="Nombre d'exemplaires", default=1)
    use_receipt = fields.Boolean(string="Tickets de caisse", default=True)
    use_bill = fields.Boolean(string="Additions", default=True)
    use_kitchen = fields.Boolean(string="Bons de préparation")
    use_report = fields.Boolean(string="Rapports X / Z", default=True)
    station_ids = fields.Many2many('adk.pos.station', string="Postes servis",
                                   help="Pour les bons de préparation : seuls les articles de ces postes sont "
                                        "imprimés ici. Vide = tous les articles.")
    config_ids = fields.Many2many('adk.pos.config', string="Points de vente",
                                  help="Vide = disponible dans tous les points de vente.")
    note = fields.Text(string="Remarques d'installation")

    @api.constrains('copies', 'port')
    def _check_numbers(self):
        for rec in self:
            if rec.copies < 1 or rec.copies > 5:
                raise UserError(_("Le nombre d'exemplaires doit être compris entre 1 et 5."))
            if rec.connection == 'network' and not (1 <= rec.port <= 65535):
                raise UserError(_("Port invalide."))

    # ------------------------------------------------------------------
    # Envoi
    # ------------------------------------------------------------------
    def _send_network(self, data):
        self.ensure_one()
        if not self.ip_address:
            raise UserError(_("Renseignez l'adresse IP de l'imprimante « %s ».") % self.name)
        try:
            with socket.create_connection((self.ip_address, self.port or 9100), timeout=5) as sock:
                sock.sendall(data)
        except OSError as exc:
            _logger.warning("Impression réseau impossible vers %s:%s : %s", self.ip_address, self.port, exc)
            raise UserError(_("Impossible de joindre l'imprimante « %s » (%s:%s) : %s. "
                              "Le serveur Odoo doit pouvoir atteindre l'imprimante sur le réseau "
                              "(même réseau local ou VPN). Sinon, utilisez le mode USB, Bluetooth ou "
                              "passerelle locale.") % (self.name, self.ip_address, self.port, exc))

    def _dispatch(self, data):
        """Envoie les octets si l'imprimante est pilotée par le serveur ; sinon les renvoie au
        terminal (navigateur) qui les transmet par USB / série / passerelle."""
        self.ensure_one()
        payload = data * self.copies if self.copies > 1 else data
        if self.connection == 'network':
            self._send_network(payload)
            return {'mode': 'done', 'printer': self.name}
        if self.connection == 'browser':
            return {'mode': 'browser', 'printer': self.name}
        return {
            'mode': self.connection, 'printer': self.name, 'data': base64.b64encode(payload).decode(),
            'bridge_url': self.bridge_url, 'baud': self.serial_baud or 9600, 'printer_id': self.id, 'bridge_printer': self.bridge_printer_name or self.name,
        }

    # ------------------------------------------------------------------
    # Mise en page des tickets
    # ------------------------------------------------------------------
    def _doc(self):
        self.ensure_one()
        return EscPos(width=int(self.paper_width), codec=self.codepage)

    @staticmethod
    def _money(order_or_cur, value):
        cur = order_or_cur.currency_id
        number = '{:,.{d}f}'.format(value or 0.0, d=cur.decimal_places).replace(',', ' ')
        return "%s %s" % (number, cur.symbol) if cur.position == 'after' else "%s %s" % (cur.symbol, number)

    def _build_receipt(self, order, is_bill=False, basic=False):
        self.ensure_one()
        d = self._doc()
        cfg = order.config_id
        m = lambda v: self._money(order, v)
        d.align('center').bold().size(2, 2).text(order.company_id.name or cfg.name).size(1, 1).bold(False)
        if cfg.receipt_header:
            d.text(cfg.receipt_header)
        d.line('=').align('left')
        d.bold().text(_("ADDITION") if is_bill else _("TICKET DE CAISSE")).bold(False)
        d.cols(order.name, fields.Datetime.to_string(fields.Datetime.context_timestamp(
            self, order.date_paid or order.date_order))[:16])
        if order.table_id:
            d.text(_("Table : %s") % order.table_id.name)
        if order.server_id:
            d.text(_("Servi par : %s") % order.server_id.name)
        d.line()
        for line in order.order_line_ids:
            qty = ('%g' % line.qty)
            if basic:
                d.text("%s x %s" % (qty, line.name))
            else:
                d.cols("%s x %s" % (qty, line.name), m(line.price_total))
                if line.discount:
                    d.text("   " + _("Remise %s %%") % ('%g' % line.discount))
            if line.note:
                d.text("   * %s" % line.note)
        d.line()
        if not basic:
            if order.amount_tax:
                d.cols(_("Sous-total HT"), m(order.amount_untaxed))
                d.cols(_("Taxes"), m(order.amount_tax))
            if order.tip_amount:
                d.cols(_("Pourboire"), m(order.tip_amount))
            d.bold().size(1, 2).cols(_("TOTAL"), m(order.amount_total + order.tip_amount)).size(1, 1).bold(False)
            for pay in order.payment_ids:
                d.cols(pay.payment_method_id.name, m(pay.tendered if pay.is_cash else pay.amount))
                if pay.change_given:
                    d.cols(_("Monnaie rendue"), m(pay.change_given))
        if cfg.receipt_footer:
            d.line().align('center').text(cfg.receipt_footer)
        d.align('left')
        if self.open_drawer and not is_bill and any(p.is_cash for p in order.payment_ids):
            d.drawer()
        if self.cut_paper:
            d.cut()
        else:
            d.feed(4)
        return d.bytes()

    def _build_kitchen(self, order, items):
        self.ensure_one()
        d = self._doc()
        d.align('center').bold().size(2, 2)
        d.text(_("Table %s") % order.table_id.name if order.table_id else dict(
            order._fields['order_type'].selection).get(order.order_type, ''))
        d.size(1, 1).bold(False)
        d.text("%s  -  %s" % (order.name, fields.Datetime.to_string(fields.Datetime.context_timestamp(
            self, fields.Datetime.now()))[11:16]))
        d.align('left').line('=')
        courses = dict(self.env['adk.pos.kitchen.item']._fields['course'].selection)
        current = None
        for item in items.sorted(lambda i: (i.course or 'other', i.id)):
            if item.course != current:
                current = item.course
                d.bold().text("-- %s --" % courses.get(current or 'other', '')).bold(False)
            d.bold().size(1, 2).text("%g x %s" % (item.qty, item.name)).size(1, 1).bold(False)
            if item.note:
                d.text("   >> %s" % item.note)
        if order.note:
            d.line().text(_("Note : %s") % order.note)
        d.beep()
        d.cut() if self.cut_paper else d.feed(4)
        return d.bytes()

    def _build_report(self, session):
        self.ensure_one()
        z = session.get_z_report_data()
        f = z['fmt']
        d = self._doc()
        d.align('center').bold().size(2, 1).text(_("RAPPORT %s") % z['report_letter']).size(1, 1).bold(False)
        d.text(session.config_id.name).text(session.name)
        d.align('left').line('=')
        d.cols(_("Transactions"), str(z['transaction_count']))
        d.cols(_("Total des ventes"), f(z['total_amount']))
        if z['tips_total']:
            d.cols(_("Pourboires"), f(z['tips_total']))
        if z['discount_count']:
            d.cols(_("Remises (%s)") % z['discount_count'], f(z['discount_amount']))
        if z['refund_count']:
            d.cols(_("Remboursements (%s)") % z['refund_count'], f(z['refund_amount']))
        d.line().bold().text(_("VENTES PAR CATÉGORIE")).bold(False)
        for c in z['sales_by_category']:
            d.cols(c['name'], f(c['total']))
        if z['tax_lines']:
            d.line().bold().text(_("TAXES")).bold(False)
            for t in z['tax_lines']:
                d.cols(t['name'], f(t['amount']))
        d.line().bold().text(_("PAIEMENTS")).bold(False)
        for p in z['payment_lines']:
            d.cols("%s (%s)" % (p['name'], p['count']), f(p['amount']))
        d.cols(_("Total encaissé"), f(z['payments_total']))
        d.line().bold().text(_("CONTRÔLE DE CAISSE")).bold(False)
        for c in z['session_control']:
            d.cols(c['name'], f(c['counted']))
            if c['difference']:
                d.cols("  " + _("Écart"), f(c['difference']))
        d.cut() if self.cut_paper else d.feed(4)
        return d.bytes()

    def _build_test(self):
        self.ensure_one()
        d = self._doc()
        d.align('center').bold().size(2, 2).text("ADK POS").size(1, 1).bold(False)
        d.text(_("Test d'impression")).text(self.name).line('=').align('left')
        d.text(_("Accents : é è ê à ç ù ô î ï ü œ €"))
        d.text(_("Largeur : %s caractères") % self.paper_width)
        d.text("0123456789" * 5)
        d.cols(_("Gauche"), _("Droite")).line()
        d.align('center').qr("https://www.odoo.com").text(_("Imprimante prête")).align('left')
        if self.open_drawer:
            d.drawer()
        d.cut() if self.cut_paper else d.feed(4)
        return d.bytes()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_test_print(self):
        self.ensure_one()
        if self.connection != 'network':
            return {
                'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _("Test d'impression"), 'type': 'info', 'sticky': True,
                           'message': _("Ce mode est piloté par le terminal : ouvrez le terminal, menu ☰ → "
                                        "« Imprimantes » → « Tester » à côté de « %s ».") % self.name},
            }
        self._dispatch(self._build_test())
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _("Test d'impression"), 'type': 'success',
                           'message': _("Page de test envoyée à « %s ».") % self.name}}

    # ------------------------------------------------------------------
    # API terminal
    # ------------------------------------------------------------------
    @api.model
    def terminal_printers(self, config_id):
        printers = self.sudo().search([]).filtered(lambda p: not p.config_ids or config_id in p.config_ids.ids)
        return [{'id': p.id, 'name': p.name, 'connection': p.connection,
                 'use_receipt': p.use_receipt, 'use_bill': p.use_bill, 'use_kitchen': p.use_kitchen,
                 'use_report': p.use_report, 'open_drawer': p.open_drawer} for p in printers]

    @api.model
    def terminal_print(self, config_id, job, record_id, extra=None, printer_id=False):
        """Prépare et (si piloté par le serveur) envoie un travail d'impression.

        job : receipt | basic_receipt | bill | kitchen | report | test | drawer
        Retourne la liste des actions que le terminal doit encore exécuter (USB, série, passerelle,
        navigateur). Une liste vide signifie que tout a été imprimé par le serveur.
        """
        if not self.env.user.has_group('adk_pos.group_adk_pos_server'):
            raise UserError(_("Accès refusé."))
        extra = extra or {}
        flag = {'receipt': 'use_receipt', 'basic_receipt': 'use_receipt', 'bill': 'use_bill',
                'kitchen': 'use_kitchen', 'report': 'use_report', 'test': None, 'drawer': None}[job]
        printers = self.sudo().search([]).filtered(lambda p: not p.config_ids or config_id in p.config_ids.ids)
        if printer_id:
            printers = printers.filtered(lambda p: p.id == printer_id)
        if flag:
            printers = printers.filtered(lambda p: p[flag])
        Order = self.env['adk.pos.order'].sudo()
        actions, errors = [], []
        for printer in printers:
            try:
                if job in ('receipt', 'basic_receipt', 'bill'):
                    order = Order.browse(record_id).exists()
                    data = printer._build_receipt(order, is_bill=(job == 'bill'), basic=(job == 'basic_receipt'))
                elif job == 'kitchen':
                    order = Order.browse(record_id).exists()
                    items = order.kitchen_item_ids.filtered(
                        lambda i: i.batch_no == extra.get('batch') and i.state != 'cancelled')
                    if printer.station_ids:
                        items = items.filtered(lambda i: i.station_id in printer.station_ids)
                    if not items:
                        continue
                    data = printer._build_kitchen(order, items)
                elif job == 'report':
                    data = printer._build_report(self.env['adk.pos.session'].sudo().browse(record_id).exists())
                elif job == 'drawer':
                    data = bytes(EscPos(codec=printer.codepage).drawer().bytes())
                else:
                    data = printer._build_test()
                actions.append(printer._dispatch(data))
            except UserError as exc:
                errors.append({'printer': printer.name, 'error': str(exc.args[0] if exc.args else exc)})
        return {'actions': [a for a in actions if a['mode'] != 'done'],
                'printed': [a['printer'] for a in actions if a['mode'] == 'done'],
                'errors': errors, 'has_printers': bool(printers)}
