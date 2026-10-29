# -*- coding: utf-8 -*-
{
    'name': "ADK Point de Vente & Restaurant",
    'version': '18.0.5.1.0',
    'category': 'Sales/Point of Sale',
    'summary': "Caisse tactile pour restaurants, bars, pâtisseries, fast-foods et boutiques (vêtements, chaussures…) : plan de salle, cuisine & bar, factures clients, imprimantes, sonneries — articles d'exemple inclus",
    'description': """
ADK Point de Vente & Restaurant
===============================

Caisse tactile, salle, cuisine, réservations, facturation, paiements et rapports pour Odoo 18.

Prêt en une minute
------------------

* Articles d'exemple catégorisés avec images pour Restaurant, Café, Bar, Fast-food, Commerce et Boutique de vêtements.
* Assistant de démarrage : type d'activité, nombre de tables, écrans de préparation, code PIN.
* Réglages simples dans le menu Paramètres.

Salle et caisse
---------------

* Tables générées automatiquement, image de fond du plan, éditeur glisser-déposer.
* Écrans Cuisine et Bar par catégories, avec accès par personne et sonnerie.
* Client recherchable, facture client Odoo, paiement à crédit, client divers modifiable.
* Commandes et sessions verrouillées après validation, convives saisis à l'ouverture de la table.
* Analyse des ventes (PDF) : présentation du rapport X/Z sur une période, par catégorie et par article, sans la liste des commandes ; liste des commandes en PDF / Excel ; rapports X/Z détaillés.
* Alarme sonore continue sur chaque écran de préparation jusqu'au bouton « Reçu » ; 10 sonneries au choix, ou votre propre fichier son importé depuis l'ordinateur (onglet « Sonneries » du point de vente).
* Plan de salle d'exemple réaliste (cuisine, bar, entrée) avec 14 tables déjà placées, proposé par l'assistant de démarrage.

Matériel
--------

* Imprimantes ESC/POS : navigateur, réseau, USB, Bluetooth/série, passerelle locale.
* Terminaux de paiement et Mobile Money via une API HTTP configurable.

Support
-------

Kambeu Henang Ange Duval — duvalkambeu61@gmail.com
""",
    'author': "Kambeu Henang Ange Duval",
    'maintainer': "Kambeu Henang Ange Duval",
    'support': 'duvalkambeu61@gmail.com',
    'website': "",
    'images': ['static/description/banner.png', 'static/description/screenshots/03-plan-de-salle.png', 'static/description/screenshots/04-prise-de-commande.png', 'static/description/screenshots/08-editeur-plan.png', 'static/description/screenshots/11-ecrans-preparation.png', 'static/description/screenshots/12-tableau-de-bord.png', 'static/description/screenshots/62-pdf-rapport-z.png'],
    'license': 'LGPL-3',
    'post_init_hook': 'post_init_hook',
    'depends': ['base', 'mail', 'web', 'product', 'account'],
    'data': [
        'security/adk_pos_security.xml',
        'security/ir.model.access.csv',
        'data/adk_pos_sequence.xml',
        'data/adk_pos_payment_methods.xml',
        'data/adk_pos_categories.xml',
        'data/adk_pos_stations.xml',
        'data/adk_pos_products.xml',
        'data/adk_pos_cron.xml',
        'report/adk_pos_receipt_templates.xml',
        'report/adk_pos_sales_detail_templates.xml',
        'report/adk_pos_z_report_templates.xml',
        'report/adk_pos_kiosk_templates.xml',
        'views/adk_pos_actions_client.xml',
        'views/adk_pos_employee_wizard_views.xml',
        'views/adk_pos_config_views.xml',
        'views/adk_pos_printer_views.xml',
        'views/adk_pos_setup_wizard_views.xml',
        'views/adk_pos_table_wizard_views.xml',
        'views/adk_pos_table_views.xml',
        'views/adk_pos_session_views.xml',
        'views/adk_pos_order_views.xml',
        'views/adk_pos_reservation_views.xml',
        'views/adk_pos_analysis_views.xml',
        'views/adk_pos_payment_method_views.xml',
        'views/adk_pos_pin_wizard_views.xml',
        'views/adk_pos_user_views.xml',
        'views/adk_pos_product_views.xml',
        'views/adk_pos_menus.xml',
        'views/adk_pos_sales_detail_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'adk_pos/static/src/scss/*.scss',
            'adk_pos/static/src/js/**/*.js',
            'adk_pos/static/src/xml/**/*.xml',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
