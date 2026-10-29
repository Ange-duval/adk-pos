# -*- coding: utf-8 -*-
from collections import defaultdict
from datetime import datetime, timedelta

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError


ADK_RINGTONES = [
    ('cloche', "1. Cloche classique"),
    ('ding', "2. Ding-dong"),
    ('carillon', "3. Carillon"),
    ('gong', "4. Gong"),
    ('bips', "5. Bips rapides"),
    ('alarme', "6. Alarme"),
    ('xylophone', "7. Xylophone"),
    ('fanfare', "8. Fanfare"),
    ('coucou', "9. Coucou"),
    ('telephone', "10. Téléphone"),
    ('custom', "11. Ma sonnerie (fichier importé)"),
]


class AdkPosConfig(models.Model):
    _name = 'adk.pos.config'
    _description = "Configuration du Point de Vente"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, name'

    name = fields.Char(string="Nom du point de vente", required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer(string="Couleur")
    image_1920 = fields.Image(string="Image / Logo")

    pos_type = fields.Selection([
        ('restaurant', 'Restaurant'),
        ('cafe', 'Café'),
        ('bar', 'Bar'),
        ('fastfood', 'Fast-food'),
        ('retail', 'Commerce de détail (épicerie, supérette)'),
        ('boutique', 'Boutique (vêtements, chaussures, accessoires)'),
    ], string="Type de point de vente", required=True, default='restaurant', tracking=True,
        help="Détermine le comportement du module : gestion de salle/tables pour "
             "Restaurant/Café/Bar/Fast-food, vente directe pour Commerce de détail et Boutique.")

    use_tables = fields.Boolean(string="Gestion de salle (tables)", compute='_compute_use_tables',
                                store=True, readonly=False)
    floor_ids = fields.One2many('adk.pos.floor', 'config_id', string="Salles / Zones")

    currency_id = fields.Many2one('res.currency', string="Devise",
                                  default=lambda self: self.env.company.currency_id)
    company_id = fields.Many2one('res.company', string="Société",
                                 default=lambda self: self.env.company)

    session_ids = fields.One2many('adk.pos.session', 'config_id', string="Sessions")
    session_count = fields.Integer(compute='_compute_session_count')
    order_count = fields.Integer(compute='_compute_order_count')
    reservation_count = fields.Integer(compute='_compute_reservation_count')

    manager_id = fields.Many2one('res.users', string="Manager responsable")
    server_ids = fields.Many2many('res.users', 'adk_pos_config_server_rel',
                                  'config_id', 'user_id', string="Serveurs assignés")
    cashier_ids = fields.Many2many('res.users', 'adk_pos_config_cashier_rel',
                                   'config_id', 'user_id', string="Caissiers assignés")

    receipt_header = fields.Text(string="En-tête du ticket")
    receipt_footer = fields.Text(string="Pied de ticket")
    auto_open_session = fields.Boolean(string="Ouverture automatique de session le matin", default=False)
    auto_print_receipt = fields.Boolean(string="Ouvrir le ticket après chaque paiement", default=False)

    payment_method_ids = fields.Many2many('adk.pos.payment.method', string="Modes de paiement acceptés")

    kiosk_enabled = fields.Boolean(
        string="Activer le menu QR & Kiosque",
        help="Permet aux clients de commander eux-mêmes en scannant un QR code ou via un lien.")
    kiosk_auto_send = fields.Boolean(
        string="Envoyer directement en cuisine",
        help="Si décoché (recommandé), les commandes clients arrivent en attente et un serveur "
             "doit les valider avant l'envoi en cuisine.")

    # ----- Commandes -----
    allow_takeaway = fields.Boolean(string="Vente à emporter", default=True)
    allow_delivery = fields.Boolean(string="Livraison", default=True)
    tip_enabled = fields.Boolean(string="Autoriser les pourboires", default=True)
    allow_price_change = fields.Boolean(string="Autoriser la modification du prix en caisse", default=False)
    quick_note_ids = fields.Many2many('adk.pos.quick.note', string="Notes rapides",
                                      help="Boutons de notes prêtes à l'emploi (ex : sans oignons, bien cuit).")
    station_ids = fields.Many2many('adk.pos.station', string="Postes de préparation")

    # ----- Validations managers -----
    approve_discount_above = fields.Float(
        string="Remise nécessitant un manager (%)", default=20.0,
        help="Toute remise supérieure à ce pourcentage demande le code PIN d'un manager. "
             "Mettre 0 pour exiger un manager sur toutes les remises.")
    approve_refund = fields.Boolean(string="Remboursement : code PIN manager obligatoire", default=True)
    approve_void = fields.Boolean(
        string="Annulation d'articles déjà envoyés : code PIN manager obligatoire", default=True)
    max_cash_difference = fields.Monetary(
        string="Écart de caisse toléré sans manager", currency_field='currency_id', default=0.0,
        help="Au-delà de cet écart (en plus ou en moins), la fermeture de caisse demande un manager. "
             "0 = aucun contrôle.")
    blind_closing = fields.Boolean(
        string="Clôture à l'aveugle", default=False,
        help="Le caissier compte son tiroir sans voir le montant théorique.")
    cash_denominations = fields.Char(
        string="Billets et pièces", default="10000,5000,2000,1000,500,100,50,25",
        help="Valeurs séparées par des virgules, utilisées pour le comptage du tiroir-caisse.")

    # ----- Salle & addition -----
    allow_bill_print = fields.Boolean(
        string="Imprimer l'addition avant le paiement", default=True,
        help="Permet de présenter l'addition au client avant l'encaissement.")
    allow_split = fields.Boolean(
        string="Autoriser le partage d'addition", default=True,
        help="Affiche le bouton « Séparer l'addition » (par lignes) dans la commande.")
    takeaway_fiscal_position_id = fields.Many2one(
        'account.fiscal.position', string="Position fiscale « à emporter / livraison »",
        help="Appliquée automatiquement aux commandes à emporter et en livraison (ex : TVA réduite "
             "sur place / à emporter). Laisser vide pour ne rien changer.")

    # ----- Facturation client -----
    invoice_mode = fields.Selection([
        ('always', "Toujours (au « Client divers » si aucun client)"),
        ('customer', "Seulement si un client est renseigné"),
        ('off', "Pas de facture"),
    ], string="Facturation", default='always', required=True,
        help="Une facture client Odoo est créée pour chaque commande encaissée. "
             "« Toujours » : sans client, la facture est établie au client divers et le client peut être "
             "renseigné plus tard (même session fermée). Un paiement « À crédit » laisse la facture non payée.")
    walkin_partner_id = fields.Many2one(
        'res.partner', string="Client divers",
        default=lambda self: self.env.ref('adk_pos.partner_walkin', raise_if_not_found=False),
        help="Client utilisé quand aucun client n'est renseigné sur la commande.")
    invoice_journal_id = fields.Many2one(
        'account.journal', string="Journal de vente", domain="[('type', '=', 'sale')]",
        help="Journal des factures. Vide : le journal de vente par défaut.")

    # ----- Commande autonome (QR / kiosque) -----
    kiosk_mode = fields.Selection([
        ('qr_order', "Menu QR + commande"),
        ('qr_menu', "Menu QR seulement (consultation)"),
    ], string="Mode du QR / kiosque", default='qr_order')
    kiosk_service = fields.Selection([
        ('table', "Service à la table"),
        ('counter', "Retrait au comptoir"),
    ], string="Service", default='table')
    kiosk_header_image = fields.Image(string="Image d'en-tête", max_width=1920, max_height=600)
    kiosk_splash_ids = fields.Many2many(
        'ir.attachment', 'adk_pos_config_splash_rel', 'config_id', 'attachment_id',
        string="Écrans d'accueil (images)",
        help="Images présentées en haut de la page de commande client (la première est affichée).")
    kiosk_welcome_text = fields.Char(string="Message d'accueil")

    # ----- Interface du terminal -----
    ui_theme = fields.Selection([('light', "Clair"), ('dark', "Sombre")], string="Thème du terminal", default='light')
    big_scrollbars = fields.Boolean(
        string="Grandes barres de défilement",
        help="Améliore la navigation sur les écrans tactiles peu précis (écrans industriels).")
    hide_images = fields.Boolean(string="Masquer les images des produits",
                                 help="Affiche des vignettes colorées à la place des photos : plus rapide sur petit réseau.")
    limit_categories = fields.Boolean(string="Limiter les catégories",
                                      help="N'afficher dans ce point de vente que certaines catégories de produits.")
    category_ids = fields.Many2many('product.category', 'adk_pos_config_category_rel', 'config_id', 'category_id',
                                    string="Catégories disponibles")
    show_margins = fields.Boolean(string="Afficher les coûts et marges (managers)",
                                  help="Montre le coût et la marge dans la fiche d'une ligne de commande.")
    sort_cart_by_category = fields.Boolean(string="Trier le panier par catégorie")

    # ----- Sonneries -----
    ring_order = fields.Selection(ADK_RINGTONES, string="Sonnerie « nouveau ticket »", default='cloche', required=True,
                                  help="Jouée sur les écrans de préparation (Cuisine, Bar…) à l'arrivée d'une commande.")
    ring_ready = fields.Selection(ADK_RINGTONES, string="Sonnerie « plat prêt »", default='ding', required=True,
                                  help="Jouée dans la salle quand la cuisine ou le bar termine une préparation.")
    ring_custom_order = fields.Binary(string="Fichier « nouveau ticket »", attachment=True,
                                      help="Importez un son depuis votre ordinateur (mp3, wav, ogg, m4a — 2 Mo maximum).")
    ring_custom_order_name = fields.Char(string="Nom du fichier « nouveau ticket »")
    ring_custom_ready = fields.Binary(string="Fichier « plat prêt »", attachment=True,
                                      help="Importez un son depuis votre ordinateur (mp3, wav, ogg, m4a — 2 Mo maximum).")
    ring_custom_ready_name = fields.Char(string="Nom du fichier « plat prêt »")
    ring_order_url = fields.Char(compute='_compute_ring_urls')
    ring_ready_url = fields.Char(compute='_compute_ring_urls')
    ring_alarm_seconds = fields.Integer(
        string="Durée de chaque alarme (secondes)", default=120,
        help="Sur les écrans de préparation, la sonnerie joue en boucle pendant cette durée "
             "(2 minutes par défaut) ou jusqu'au clic sur « J'ai vu ». De 10 à 600 secondes.")
    ring_repeat = fields.Boolean(
        string="Relancer l'alarme tant que le ticket n'est pas vu", default=True,
        help="Si la durée est écoulée et que personne n'a cliqué sur « J'ai vu », l'alarme repart "
             "automatiquement, sans limite.")
    ring_volume = fields.Integer(string="Volume (%)", default=100,
                                 help="De 10 à 100 %.")

    @api.depends('ring_order', 'ring_ready', 'ring_custom_order', 'ring_custom_ready', 'write_date')
    def _compute_ring_urls(self):
        for config in self:
            stamp = fields.Datetime.to_string(config.write_date) or ''
            for kind in ('order', 'ready'):
                field = 'ring_custom_%s' % kind
                url = ''
                if config['ring_%s' % kind] == 'custom' and config.id and config[field]:
                    url = '/web/content/adk.pos.config/%d/%s?unique=%s' % (config.id, field, stamp)
                config['ring_%s_url' % kind] = url

    @api.constrains('ring_order', 'ring_ready', 'ring_custom_order', 'ring_custom_ready',
                    'ring_custom_order_name', 'ring_custom_ready_name')
    def _check_ring_custom(self):
        allowed = ('.mp3', '.wav', '.ogg', '.m4a', '.aac', '.webm')
        for config in self:
            for kind in ('order', 'ready'):
                if config['ring_%s' % kind] == 'custom' and not config['ring_custom_%s' % kind]:
                    raise UserError(_("Importez un fichier son pour la sonnerie « Ma sonnerie », "
                                      "ou choisissez l'une des 10 sonneries proposées."))
                name = (config['ring_custom_%s_name' % kind] or '').lower()
                if config['ring_custom_%s' % kind]:
                    if name and not name.endswith(allowed):
                        raise UserError(_("Format de son non pris en charge : utilisez mp3, wav, ogg ou m4a."))
                    # taille décodée ≈ 3/4 de la taille base64
                    size = len(config['ring_custom_%s' % kind]) * 3 // 4
                    if size > 2 * 1024 * 1024:
                        raise UserError(_("Le fichier son est trop lourd (2 Mo maximum)."))

    @api.constrains('ring_alarm_seconds')
    def _check_ring_alarm_seconds(self):
        for config in self:
            if not 10 <= config.ring_alarm_seconds <= 600:
                raise UserError(_("La durée de l'alarme doit être comprise entre 10 et 600 secondes."))

    @api.constrains('ring_volume')
    def _check_ring_volume(self):
        for config in self:
            if not 10 <= config.ring_volume <= 100:
                raise UserError(_("Le volume doit être compris entre 10 et 100 %."))

    def action_listen_ring(self):
        """Bouton « Écouter » : joue une sonnerie dans le navigateur sans quitter le formulaire."""
        self.ensure_one()
        ring = self.env.context.get('ring') or self.ring_order
        url = ''
        if ring == 'custom':
            url = self.ring_ready_url if self.env.context.get('ring_kind') == 'ready' else self.ring_order_url
        return {'type': 'ir.actions.client', 'tag': 'adk_pos_play_ring',
                'params': {'ring': ring, 'volume': self.ring_volume, 'url': url}}

    def action_listen_ready(self):
        self.ensure_one()
        return self.with_context(ring=self.ring_ready, ring_kind='ready').action_listen_ring()

    # ----- Prix & remises -----
    line_discount_enabled = fields.Boolean(string="Remise par ligne", default=True)
    global_discount_enabled = fields.Boolean(string="Remise globale sur la commande", default=True)

    # ----- Tickets & impression -----
    order_prefix = fields.Char(string="Préfixe des numéros de commande",
                               help="Remplace « CMD » dans le numéro des commandes de ce point de vente (ex : TDE).")
    print_kitchen_on_send = fields.Boolean(string="Imprimer le bon de préparation à l'envoi", default=True,
                                           help="Utilise les imprimantes « bons de préparation » configurées.")
    printer_ids = fields.Many2many('adk.pos.printer', 'adk_pos_config_adk_pos_printer_rel',
                                   'adk_pos_config_id', 'adk_pos_printer_id', string="Imprimantes")
    customer_display = fields.Boolean(string="Afficheur client (second écran)",
                                      help="Active la page d'affichage client à ouvrir sur un 2e écran ou une tablette.")
    customer_display_url = fields.Char(compute='_compute_customer_display_url', string="Adresse de l'afficheur client")

    def _compute_customer_display_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        for cfg in self:
            cfg.customer_display_url = "%s/adk_pos/customer_display/%s" % (base, cfg.id) if cfg.id else False

    # ----- Réservations -----
    reservation_enabled = fields.Boolean(string="Gestion des réservations", default=True)
    reservation_duration = fields.Integer(string="Durée par défaut d'une réservation (min)", default=90)
    reservation_lead_minutes = fields.Integer(
        string="Bloquer la table avant l'arrivée (min)", default=45,
        help="La table apparaît « Réservée » sur le plan de salle ce nombre de minutes avant l'heure.")
    reservation_grace_minutes = fields.Integer(
        string="Délai avant « Non présenté » (min)", default=30)

    @api.depends('pos_type')
    def _compute_use_tables(self):
        for rec in self:
            rec.use_tables = rec.pos_type in ('restaurant', 'cafe', 'bar', 'fastfood')

    def _compute_session_count(self):
        for rec in self:
            rec.session_count = self.env['adk.pos.session'].search_count([('config_id', '=', rec.id)])

    def _compute_order_count(self):
        for rec in self:
            rec.order_count = self.env['adk.pos.order'].search_count([('config_id', '=', rec.id)])

    def _compute_reservation_count(self):
        for rec in self:
            rec.reservation_count = self.env['adk.pos.reservation'].search_count([
                ('config_id', '=', rec.id), ('state', '=', 'confirmed')])

    @api.constrains('cash_denominations')
    def _check_denominations(self):
        for rec in self:
            for part in (rec.cash_denominations or '').split(','):
                part = part.strip()
                if not part:
                    continue
                try:
                    if float(part) <= 0:
                        raise ValueError
                except ValueError:
                    raise UserError(_("Billets et pièces : « %s » n'est pas une valeur valide.") % part)

    def get_denominations(self):
        self.ensure_one()
        values = []
        for part in (self.cash_denominations or '').split(','):
            part = part.strip()
            if part:
                values.append(float(part))
        return sorted(set(values), reverse=True)

    def action_view_sessions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Sessions"),
            'res_model': 'adk.pos.session', 'view_mode': 'list,form',
            'domain': [('config_id', '=', self.id)],
        }

    def action_view_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Commandes"),
            'res_model': 'adk.pos.order', 'view_mode': 'list,form,kanban',
            'domain': [('config_id', '=', self.id)],
        }

    def action_view_reservations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Réservations"),
            'res_model': 'adk.pos.reservation', 'view_mode': 'calendar,list,form',
            'domain': [('config_id', '=', self.id)],
            'context': {'default_config_id': self.id},
        }

    def action_print_table_qrs(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': '/adk_pos/table_qrs/%s' % self.id, 'target': 'new'}

    # ------------------------------------------------------------------
    # Données pour le terminal
    # ------------------------------------------------------------------
    @api.model
    def get_terminal_config(self, config_id):
        """Tout ce dont le terminal a besoin pour un point de vente, en un seul appel."""
        config = self.browse(config_id).exists()
        if not config:
            raise UserError(_("Point de vente introuvable."))
        employee_ids = set(config.server_ids.ids) | set(config.cashier_ids.ids)
        if config.manager_id:
            employee_ids.add(config.manager_id.id)
        employees = self.env['res.users'].sudo().browse(sorted(employee_ids)).filtered('active')
        return {
            'id': config.id,
            'name': config.name,
            'pos_type': config.pos_type,
            'use_tables': config.use_tables,
            'kiosk_enabled': config.kiosk_enabled,
            'allow_takeaway': config.allow_takeaway,
            'allow_delivery': config.allow_delivery,
            'tip_enabled': config.tip_enabled,
            'allow_price_change': config.allow_price_change,
            'approve_discount_above': config.approve_discount_above,
            'approve_refund': config.approve_refund,
            'approve_void': config.approve_void,
            'blind_closing': config.blind_closing,
            'max_cash_difference': config.max_cash_difference,
            'reservation_enabled': config.reservation_enabled,
            'reservation_duration': config.reservation_duration,
            'auto_print_receipt': config.auto_print_receipt,
            'allow_bill_print': config.allow_bill_print,
            'invoice_mode': config.invoice_mode,
            'customer_display_url': config.customer_display_url if config.customer_display else '',
            'allow_split': config.allow_split,
            'ui_theme': config.ui_theme,
            'big_scrollbars': config.big_scrollbars,
            'hide_images': config.hide_images,
            'category_ids': config.category_ids.ids if config.limit_categories else [],
            'show_margins': config.show_margins,
            'sort_cart_by_category': config.sort_cart_by_category,
            'ring_order': config.ring_order,
            'ring_ready': config.ring_ready,
            'ring_order_url': config.ring_order_url,
            'ring_ready_url': config.ring_ready_url,
            'ring_volume': config.ring_volume,
            'ring_alarm_seconds': config.ring_alarm_seconds,
            'ring_repeat': config.ring_repeat,
            'line_discount_enabled': config.line_discount_enabled,
            'global_discount_enabled': config.global_discount_enabled,
            'print_kitchen_on_send': config.print_kitchen_on_send,
            'has_printers': bool(self.env['adk.pos.printer'].sudo().search_count(
                ['|', ('config_ids', '=', False), ('config_ids', 'in', config.id)])),
            'denominations': config.get_denominations(),
            'currency': {
                'symbol': config.currency_id.symbol,
                'position': config.currency_id.position,
                'decimals': config.currency_id.decimal_places,
            },
            'employees': [{
                'id': u.id, 'name': u.name, 'role': u._adk_pos_role(),
                'has_pin': bool(u.adk_pos_pin_hash),
            } for u in employees],
            'payment_methods': [{
                'id': m.id, 'name': m.name, 'icon': m.icon, 'is_cash': m.is_cash,
                'requires_reference': m.requires_reference,
                'terminal': m.terminal_type != 'manual', 'is_credit': m.is_credit,
            } for m in config.payment_method_ids.filtered('active').sorted('sequence')],
            'quick_notes': [n.name for n in config.quick_note_ids.filtered('active').sorted('sequence')],
            'stations': [{'id': s.id, 'name': s.name} for s in config.station_ids.filtered('active')],
        }

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    @api.model
    def _dashboard_period(self, period):
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        now_local = datetime.now(tz)
        days = {'today': 0, 'week': 6, 'month': 29}.get(period, 0)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
        start = start_local.astimezone(pytz.utc).replace(tzinfo=None)
        length = timedelta(days=days + 1)
        return tz, start, start - length

    @api.model
    def get_dashboard_data(self, config_id=False, period='today'):
        """Indicateurs clés pour le tableau de bord (période : today / week / month)."""
        tz, start, prev_start = self._dashboard_period(period)
        cfg_domain = [('config_id', '=', config_id)] if config_id else []
        Order = self.env['adk.pos.order']
        Session = self.env['adk.pos.session']
        config = self.browse(config_id).exists() if config_id else self.search([], limit=1)
        currency = config.currency_id or self.env.company.currency_id

        paid_domain = cfg_domain + [('state', '=', 'paid'), ('date_paid', '>=', start)]
        paid = Order.search(paid_domain, limit=20000)
        sales_orders = paid.filtered(lambda o: not o.refund_of_id)
        refund_orders = paid.filtered(lambda o: o.refund_of_id)
        prev_paid = Order.search(cfg_domain + [
            ('state', '=', 'paid'), ('date_paid', '>=', prev_start), ('date_paid', '<', start)], limit=20000)

        gross = sum(sales_orders.mapped('amount_total'))
        refunds = -sum(refund_orders.mapped('amount_total'))
        net = gross - refunds
        prev_net = sum(prev_paid.mapped('amount_total'))
        variation = ((net - prev_net) / prev_net * 100.0) if prev_net else 0.0

        # Ventes par heure
        by_hour = defaultdict(float)
        for order in sales_orders:
            local = pytz.utc.localize(order.date_paid).astimezone(tz)
            by_hour[local.hour] += order.amount_total
        hours = [{'hour': h, 'total': by_hour.get(h, 0.0)} for h in range(24)]

        # Paiements par mode
        by_method = defaultdict(float)
        for pay in paid.mapped('payment_ids'):
            by_method[pay.payment_method_id.name or '—'] += pay.amount
        payments = sorted(({'name': k, 'total': v} for k, v in by_method.items()),
                          key=lambda r: -r['total'])

        # Types de commande
        type_labels = dict(Order._fields['order_type'].selection)
        by_type = defaultdict(lambda: {'count': 0, 'total': 0.0})
        for order in sales_orders:
            key = type_labels.get(order.order_type, order.order_type)
            by_type[key]['count'] += 1
            by_type[key]['total'] += order.amount_total
        order_types = [{'name': k, **v} for k, v in by_type.items()]

        # Produits / serveurs / caissiers
        by_product = defaultdict(lambda: {'qty': 0.0, 'total': 0.0})
        for line in sales_orders.mapped('order_line_ids'):
            by_product[line.product_id.display_name]['qty'] += line.qty
            by_product[line.product_id.display_name]['total'] += line.price_total
        top_products = sorted(({'name': k, **v} for k, v in by_product.items()),
                              key=lambda r: -r['qty'])[:8]

        by_server = defaultdict(lambda: {'total': 0.0, 'count': 0})
        for order in sales_orders:
            by_server[order.server_id.name or '—']['total'] += order.amount_total
            by_server[order.server_id.name or '—']['count'] += 1
        top_servers = sorted(({'name': k, **v} for k, v in by_server.items()),
                             key=lambda r: -r['total'])[:6]

        by_cashier = defaultdict(lambda: {'total': 0.0, 'count': 0, 'refunds': 0.0})
        for order in sales_orders:
            by_cashier[order.cashier_id.name or '—']['total'] += order.amount_total
            by_cashier[order.cashier_id.name or '—']['count'] += 1
        for order in refund_orders:
            by_cashier[order.cashier_id.name or '—']['refunds'] += -order.amount_total
        top_cashiers = sorted(({'name': k, **v} for k, v in by_cashier.items()),
                              key=lambda r: -r['total'])[:6]

        # Salle, temps réel
        tables = self.env['adk.pos.table'].search(
            ([('config_id', '=', config_id)] if config_id else []))
        occupied = len(tables.filtered(lambda t: t.state in ('occupied', 'billing')))
        open_orders = Order.search_count(cfg_domain + [('state', 'in', ('draft', 'sent', 'billing'))])
        open_sessions = Session.search_count(cfg_domain + [('state', '=', 'opened')])

        # Cuisine
        kitchen = self.env['adk.pos.kitchen.item'].search(cfg_domain + [
            ('state', 'in', ('ready', 'served')), ('date_sent', '>=', start), ('date_ready', '!=', False)],
            limit=5000)
        prep = [(i.date_ready - i.date_sent).total_seconds() / 60.0 for i in kitchen]
        avg_prep = sum(prep) / len(prep) if prep else 0.0

        today_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        day_start = today_local.astimezone(pytz.utc).replace(tzinfo=None)
        reservations_today = self.env['adk.pos.reservation'].search_count(cfg_domain + [
            ('date_start', '>=', day_start), ('date_start', '<', day_start + timedelta(days=1)),
            ('state', 'in', ('confirmed', 'seated', 'done'))])

        return {
            'period': period,
            'currency_symbol': currency.symbol,
            'currency_position': currency.position,
            'total_sales': net,
            'gross_sales': gross,
            'refunds_total': refunds,
            'refunds_count': len(refund_orders),
            'orders_count': len(sales_orders),
            'average_ticket': (gross / len(sales_orders)) if sales_orders else 0.0,
            'tips_total': sum(sales_orders.mapped('tip_amount')),
            'discount_total': sum(sales_orders.mapped('discount_total')),
            'previous_sales': prev_net,
            'variation': variation,
            'open_sessions': open_sessions,
            'open_orders': open_orders,
            'tables_total': len(tables),
            'tables_occupied': occupied,
            'reservations_today': reservations_today,
            'avg_prep_minutes': avg_prep,
            'sales_by_hour': hours,
            'payments': payments,
            'order_types': order_types,
            'top_products': top_products,
            'top_servers': top_servers,
            'top_cashiers': top_cashiers,
            # Compatibilité avec l'ancienne version du tableau de bord
            'total_sales_today': net,
            'orders_count_today': len(sales_orders),
        }
