from odoo import _, fields, models
from odoo.exceptions import UserError


class WooSyncWizard(models.TransientModel):
    _name = 'woo.sync.wizard'
    _description = 'WooCommerce Sync Wizard'

    backend_id = fields.Many2one('woo.backend', string='Store', required=True)
    operation = fields.Selection([
        ('import_orders', 'Import Orders'),
        ('export_products', 'Export Products'),
        ('sync_stock', 'Sync Stock'),
        ('import_customers', 'Import Customers'),
    ], string='Operation', required=True)
    date_from = fields.Datetime(string='Import From Date')
    product_ids = fields.Many2many(
        'product.template',
        string='Products to Export',
        domain="[('woo_backend_id', '=', backend_id)]",
    )
    result_message = fields.Text(string='Result', readonly=True)

    def action_sync(self):
        self.ensure_one()
        backend = self.backend_id
        try:
            if self.operation == 'import_orders':
                count = backend._do_import_orders(date_from=self.date_from)
                msg = _(f'Successfully imported {count} orders.')
            elif self.operation == 'export_products':
                pids = self.product_ids.ids if self.product_ids else None
                count = backend._do_export_products(product_ids=pids)
                msg = _(f'Successfully exported {count} products.')
            elif self.operation == 'sync_stock':
                count = backend._do_sync_stock()
                msg = _(f'Successfully synced stock for {count} products.')
            elif self.operation == 'import_customers':
                count = backend._do_import_customers()
                msg = _(f'Successfully imported {count} customers.')
            else:
                raise UserError(_('Unknown operation.'))

            self.result_message = msg
        except Exception as e:
            self.result_message = f'Error: {str(e)}'

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'woo.sync.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
