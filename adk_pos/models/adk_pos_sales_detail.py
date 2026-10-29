# -*- coding: utf-8 -*-
from collections import OrderedDict, defaultdict

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AdkPosSalesDetailWizard(models.TransientModel):
    _name = 'adk.pos.sales.detail.wizard'
    _description = "Assistant : Détail des ventes"

    date_from = fields.Date(string="Du", required=True, default=fields.Date.context_today)
    date_to = fields.Date(string="Au", required=True, default=fields.Date.context_today)
    config_ids = fields.Many2many('adk.pos.config', string="Points de vente",
                                  help="Laissez vide pour tous les points de vente.")
    server_ids = fields.Many2many('res.users', 'adk_pos_sd_wiz_user_rel', 'wiz_id', 'user_id',
                                  string="Serveurs / caissiers",
                                  help="Laissez vide pour tout le personnel.")
    payment_method_ids = fields.Many2many('adk.pos.payment.method', string="Modes de paiement",
                                          help="Ne garder que les commandes réglées avec ces modes.")
    order_type = fields.Selection([
        ('dine_in', 'Sur place'), ('takeaway', 'À emporter'), ('delivery', 'Livraison'),
    ], string="Type de commande")
    show_lines = fields.Boolean(string="Afficher le détail des articles de chaque commande", default=False)
    products_per_categ = fields.Integer(
        string="Articles affichés par catégorie", default=5,
        help="Analyse des ventes (PDF) : nombre d'articles détaillés sous chaque catégorie (les plus vendus). "
             "Les autres sont regroupés sur une ligne « Autres articles » : les totaux restent exacts. "
             "Mettez 0 pour afficher tous les articles.")
    group_by = fields.Selection([
        ('day', 'Par jour'), ('server', 'Par serveur'), ('method', 'Par mode de paiement'), ('none', 'Sans regroupement'),
    ], string="Regrouper les commandes", default='day', required=True)

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for wiz in self:
            if wiz.date_to < wiz.date_from:
                raise UserError(_("La date de fin doit être postérieure à la date de début."))

    def _orders_domain(self):
        self.ensure_one()
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        start = tz.localize(fields.Datetime.to_datetime(self.date_from)).astimezone(pytz.utc).replace(tzinfo=None)
        end = tz.localize(fields.Datetime.to_datetime(self.date_to).replace(hour=23, minute=59, second=59)
                          ).astimezone(pytz.utc).replace(tzinfo=None)
        domain = [('state', '=', 'paid'), ('date_paid', '>=', start), ('date_paid', '<=', end)]
        if self.config_ids:
            domain.append(('config_id', 'in', self.config_ids.ids))
        if self.order_type:
            domain.append(('order_type', '=', self.order_type))
        if self.payment_method_ids:
            domain.append(('payment_ids.payment_method_id', 'in', self.payment_method_ids.ids))
        return domain

    def action_print_pdf(self):
        self.ensure_one()
        if not self.env['adk.pos.order'].search_count(self._orders_domain()):
            raise UserError(_("Aucune commande payée sur cette période avec ces critères."))
        return self.env.ref('adk_pos.action_report_sales_detail').report_action(self)

    def action_print_analysis_pdf(self):
        """Analyse des ventes : même présentation que le rapport X/Z, sur la période, sans la liste des commandes."""
        self.ensure_one()
        if not self.env['adk.pos.order'].search_count(self._orders_domain()):
            raise UserError(_("Aucune commande payée sur cette période avec ces critères."))
        return self.env.ref('adk_pos.action_report_sales_analysis').report_action(self)

    def action_open_list(self):
        """Liste interactive : filtrer, regrouper, exporter vers Excel (menu Action ▸ Exporter)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Détail des ventes"),
            'res_model': 'adk.pos.order', 'view_mode': 'list,pivot,form',
            'domain': self._orders_domain(), 'context': {'group_by': ['config_id']},
        }

    # ------------------------------------------------------------------
    def _get_report_data(self):
        """Prépare toutes les données du PDF (une seule passe, adapté à plusieurs milliers de commandes)."""
        self.ensure_one()
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        type_labels = dict(self.env['adk.pos.order']._fields['order_type'].selection)
        cur = self.env.company.currency_id

        def fmt(value):
            number = '{:,.{d}f}'.format(value or 0.0, d=cur.decimal_places).replace(',', '\u202f')
            return "%s %s" % (number, cur.symbol) if cur.position == 'after' else "%s %s" % (cur.symbol, number)
        orders = self.env['adk.pos.order'].search(self._orders_domain(), order='date_paid, id')
        if self.server_ids:
            orders = orders.filtered(lambda o: o.server_id in self.server_ids or o.cashier_id in self.server_ids)

        def local(dt):
            return pytz.utc.localize(dt).astimezone(tz) if dt else None

        rows = []
        by_method = defaultdict(lambda: {'count': 0, 'amount': 0.0})
        by_server = defaultdict(lambda: {'count': 0, 'total': 0.0})
        by_day = OrderedDict()
        totals = {'count': 0, 'total': 0.0, 'refund_count': 0, 'refund_total': 0.0,
                  'guests': 0, 'discount': 0.0, 'tips': 0.0, 'credit': 0.0}
        for order in orders:
            when = local(order.date_paid or order.date_order)
            methods = order.payment_ids.mapped('payment_method_id')
            is_refund = bool(order.refund_of_id)
            row = {
                'name': order.name, 'day': when.strftime('%d/%m/%Y') if when else '',
                'time': when.strftime('%H:%M') if when else '',
                'table': order.table_id.name or type_labels.get(order.order_type, ''),
                'type': type_labels.get(order.order_type, ''),
                'server': order.server_id.name or '—', 'partner': order.partner_id.name or '',
                'methods': ', '.join(methods.mapped('name')), 'method_ids': methods.ids,
                'total': order.amount_total, 'guests': order.guests,
                'is_refund': is_refund,
                'invoice': (order.invoice_id or order.credit_note_id).name or '',
                'lines': [{'qty': l.qty, 'name': l.product_id.display_name, 'total': l.price_total}
                          for l in order.order_line_ids] if self.show_lines else [],
            }
            rows.append(row)
            totals['total'] += order.amount_total
            if is_refund:
                totals['refund_count'] += 1
                totals['refund_total'] += order.amount_total
            else:
                totals['count'] += 1
                totals['guests'] += order.guests
                by_server[row['server']]['count'] += 1
                by_server[row['server']]['total'] += order.amount_total
            totals['discount'] += order.discount_total
            totals['tips'] += order.tip_amount
            day = by_day.setdefault(row['day'], {'count': 0, 'total': 0.0})
            day['count'] += 1
            day['total'] += order.amount_total
            for pay in order.payment_ids:
                m = by_method[pay.payment_method_id.name]
                m['count'] += 1
                m['amount'] += pay.amount
                if pay.payment_method_id.is_credit:
                    totals['credit'] += pay.amount
        # Regroupement demandé
        key = {'day': 'day', 'server': 'server', 'method': 'methods', 'none': None}[self.group_by]
        groups = OrderedDict()
        for row in rows:
            groups.setdefault(row[key] if key else _("Toutes les commandes"), []).append(row)
        groups = [{
            'title': title or '—', 'rows': grows,
            'total': sum(r['total'] for r in grows), 'count': len(grows),
        } for title, grows in (sorted(groups.items()) if self.group_by in ('server', 'method') else groups.items())]
        return {
            'wizard': self, 'company': self.env.company, 'fmt': fmt, 'rows': rows, 'groups': groups,
            'totals': totals,
            'average': (totals['total'] / totals['count']) if totals['count'] else 0.0,
            'by_method': sorted(({'name': k, **v} for k, v in by_method.items()), key=lambda m: -m['amount']),
            'by_server': sorted(({'name': k, **v} for k, v in by_server.items()), key=lambda m: -m['total']),
            'by_day': [{'name': k, **v} for k, v in by_day.items()],
            'configs': ', '.join((self.config_ids or self.env['adk.pos.config'].search([])).mapped('name')),
            'group_label': dict(self._fields['group_by'].selection)[self.group_by],
        }

    # ------------------------------------------------------------------
    def _get_analysis_data(self):
        """Analyse des ventes de la période : ventes par catégorie / article, paiements, taxes, analyse.
        Même contenu que le rapport X/Z d'une session, sans le détail commande par commande."""
        self.ensure_one()
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        type_labels = dict(self.env['adk.pos.order']._fields['order_type'].selection)
        cur = self.env.company.currency_id

        def fmt(value):
            number = '{:,.{d}f}'.format(value or 0.0, d=cur.decimal_places).replace(',', '\u202f')
            return "%s %s" % (number, cur.symbol) if cur.position == 'after' else "%s %s" % (cur.symbol, number)

        orders = self.env['adk.pos.order'].search(self._orders_domain(), order='date_paid, id')
        if self.server_ids:
            orders = orders.filtered(lambda o: o.server_id in self.server_ids or o.cashier_id in self.server_ids)
        sales_orders = orders.filtered(lambda o: not o.refund_of_id)
        refunds = orders.filtered('refund_of_id')
        lines = orders.mapped('order_line_ids')
        limit = max(self.products_per_categ, 0)

        # --- Ventes par catégorie, puis par article ---
        categories = {}
        for line in lines:
            categ = line.product_id.categ_id
            label = (categ.complete_name or categ.name or '').replace('All / ', '').strip() if categ else ''
            if not label or label == 'All':
                label = _("Sans catégorie")
            cat = categories.setdefault(categ.id if categ else 0, {'name': label, 'qty': 0.0, 'total': 0.0, 'lines': {}})
            cat['qty'] += line.qty
            cat['total'] += line.price_total
            prod = cat['lines'].setdefault(line.product_id.id, {
                'name': line.product_id.display_name,
                'uom': line.product_id.uom_id.name or _("Unité(s)"), 'qty': 0.0, 'total': 0.0})
            prod['qty'] += line.qty
            prod['total'] += line.price_total
        sales_by_category = []
        for c in sorted(categories.values(), key=lambda c: c['name']):
            products = sorted(c['lines'].values(), key=lambda l: -l['total'])
            others = None
            if limit and len(products) > limit:
                rest = products[limit:]
                products = products[:limit]
                others = {'count': len(rest), 'qty': sum(r['qty'] for r in rest),
                          'total': sum(r['total'] for r in rest)}
            sales_by_category.append({'name': c['name'], 'qty': c['qty'], 'total': c['total'],
                                      'lines': products, 'others': others})
        total_qty = sum(c['qty'] for c in sales_by_category)
        total_amount = sum(c['total'] for c in sales_by_category)

        # --- Taxes ---
        taxes = {}
        for line in lines:
            if line.tax_ids:
                for tax in line.tax_ids:
                    t = taxes.setdefault(tax.id, {'name': tax.name, 'base': 0.0, 'amount': 0.0})
                    t['base'] += line.price_subtotal
                    t['amount'] += (line.price_total - line.price_subtotal)
            else:
                t = taxes.setdefault('none', {'name': _("Pas de taxe"), 'base': 0.0, 'amount': 0.0})
                t['base'] += line.price_subtotal

        # --- Paiements ---
        pay_by_method = {}
        for pay in orders.mapped('payment_ids'):
            m = pay.payment_method_id
            p = pay_by_method.setdefault(m.id, {'name': m.name, 'is_credit': m.is_credit, 'amount': 0.0, 'count': 0})
            p['amount'] += pay.amount
            p['count'] += 1
        payment_lines = sorted(pay_by_method.values(), key=lambda p: -p['amount'])

        # --- Analyse (serveurs, caissiers, types, heures, jours) ---
        def bucket():
            return defaultdict(lambda: {'count': 0, 'total': 0.0})
        by_server, by_cashier, by_type, by_hour, by_day = bucket(), bucket(), bucket(), bucket(), OrderedDict()
        guests = 0
        for order in sales_orders:
            for store, key in ((by_server, order.server_id.name or '—'), (by_cashier, order.cashier_id.name or '—'),
                               (by_type, type_labels.get(order.order_type, ''))):
                store[key]['count'] += 1
                store[key]['total'] += order.amount_total
            guests += order.guests
            when = order.date_paid or order.date_order
            if when:
                local = pytz.utc.localize(when).astimezone(tz)
                by_hour[local.strftime('%Hh')]['count'] += 1
                by_hour[local.strftime('%Hh')]['total'] += order.amount_total
                day = by_day.setdefault(local.strftime('%d/%m/%Y'), {'count': 0, 'total': 0.0})
                day['count'] += 1
                day['total'] += order.amount_total

        def listing(store, key=None):
            items = sorted(store.items(), key=key) if key else store.items()
            return [{'name': k, **v} for k, v in items]
        discounted = lines.filtered(lambda l: l.discount)
        net_total = sum(orders.mapped('amount_total'))
        return {
            'wizard': self, 'company': self.env.company, 'fmt': fmt,
            'configs': ', '.join((self.config_ids or self.env['adk.pos.config'].search([])).mapped('name')),
            'transaction_count': len(sales_orders),
            'total_amount': net_total,
            'average': (sum(sales_orders.mapped('amount_total')) / len(sales_orders)) if sales_orders else 0.0,
            'guests': guests,
            'tips_total': sum(orders.mapped('tip_amount')),
            'credit_total': sum(p['amount'] for p in payment_lines if p['is_credit']),
            'sales_by_category': sales_by_category,
            'total_qty': total_qty, 'categ_amount': total_amount,
            'limit': limit,
            'tax_lines': list(taxes.values()),
            'payment_lines': payment_lines,
            'payments_total': sum(p['amount'] for p in payment_lines),
            'discount_count': len(discounted), 'discount_amount': sum(discounted.mapped('discount_amount')),
            'refund_count': len(refunds), 'refund_amount': -sum(refunds.mapped('amount_total')),
            'by_server': listing(by_server, lambda kv: -kv[1]['total']),
            'cashiers': listing(by_cashier, lambda kv: -kv[1]['total']),
            'order_types': listing(by_type),
            'by_hour': listing(by_hour, lambda kv: kv[0]),
            'by_day': [{'name': k, **v} for k, v in by_day.items()],
        }


class ReportSalesAnalysis(models.AbstractModel):
    _name = 'report.adk_pos.report_sales_analysis'
    _description = "Rapport Analyse des ventes"

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['adk.pos.sales.detail.wizard'].browse(docids)
        return {'doc_ids': docids, 'doc_model': 'adk.pos.sales.detail.wizard', 'docs': docs,
                'get_data': lambda w: w._get_analysis_data()}


class ReportSalesDetail(models.AbstractModel):
    _name = 'report.adk_pos.report_sales_detail'
    _description = "Rapport Détail des ventes"

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['adk.pos.sales.detail.wizard'].browse(docids)
        return {'doc_ids': docids, 'doc_model': 'adk.pos.sales.detail.wizard', 'docs': docs,
                'get_data': lambda w: w._get_report_data()}
