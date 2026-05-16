{
    'name': 'WooCommerce Connector',
    'version': '19.0.1.0.0',
    'category': 'eCommerce',
    'summary': 'Sync Products, Orders, Customers and Inventory between WooCommerce and Odoo',
    'description': """
WooCommerce Connector — by Technical Rajni
==========================================
Two-way sync between WooCommerce and Odoo.

Features:
- Multiple WooCommerce stores support
- Export Products from Odoo to WooCommerce
- Import Orders from WooCommerce as Sale Orders
- Import Customers from WooCommerce as Contacts
- Sync Inventory / Stock levels
- Product Categories sync
- Auto-sync with Scheduled Actions
- Sync logs and error tracking
- Test Connection button
    """,
    'author': 'Technical Rajni',
    'website': 'https://www.technicalrajni.com',
    'license': 'OPL-1',
    'depends': ['sale_management', 'stock', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'data/scheduled_actions.xml',
        'views/woo_log_views.xml',
        'views/product_views.xml',
        'views/sale_order_views.xml',
        'wizard/woo_sync_wizard_views.xml',
        'views/woo_backend_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
    'price': 149.00,
    'currency': 'USD',
}
