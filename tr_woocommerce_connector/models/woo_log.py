from odoo import fields, models


class WooLog(models.Model):
    _name = 'woo.log'
    _description = 'WooCommerce Sync Log'
    _order = 'create_date desc'
    _rec_name = 'operation'

    backend_id = fields.Many2one('woo.backend', string='Store', ondelete='cascade')
    operation = fields.Selection([
        ('import_orders', 'Import Orders'),
        ('import_products', 'Import Products'),
        ('import_customers', 'Import Customers'),
        ('export_products', 'Export Products'),
        ('sync_stock', 'Sync Stock'),
    ], string='Operation')
    status = fields.Selection([
        ('success', 'Success'),
        ('error', 'Error'),
        ('warning', 'Warning'),
    ], string='Status')
    message = fields.Text(string='Message')
    woo_id = fields.Char(string='WooCommerce ID')
    create_date = fields.Datetime(string='Date', readonly=True)
