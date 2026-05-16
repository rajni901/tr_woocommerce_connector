from odoo import _, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    woo_id = fields.Char(string='WooCommerce ID', copy=False, index=True)
    woo_backend_id = fields.Many2one('woo.backend', string='WooCommerce Store', copy=False)
    woo_last_sync = fields.Datetime(string='Last WooCommerce Sync', readonly=True)

    def _prepare_woo_product_data(self):
        self.ensure_one()
        data = {
            'name': self.name,
            'type': 'simple',
            'regular_price': str(self.list_price),
            'description': self.description or '',
            'short_description': self.description_sale or '',
            'manage_stock': True,
            'stock_quantity': int(self.qty_available),
        }
        if self.default_code:
            data['sku'] = self.default_code
        return data

    def action_export_to_woocommerce(self):
        backend = self.mapped('woo_backend_id')
        if not backend:
            raise Exception('No WooCommerce store linked to this product.')
        backend = backend[0]
        for product in self:
            product._export_to_woocommerce(backend)

    def _export_to_woocommerce(self, backend):
        data = self._prepare_woo_product_data()
        try:
            if self.woo_id:
                result = backend._api_put(f'products/{self.woo_id}', data)
            else:
                result = backend._api_post('products', data)
                self.write({
                    'woo_id': str(result['id']),
                    'woo_backend_id': backend.id,
                    'woo_last_sync': fields.Datetime.now(),
                })
            backend._log('export_products', 'success',
                         f'Product "{self.name}" exported. WooID: {self.woo_id}',
                         self.woo_id)
        except Exception as e:
            backend._log('export_products', 'error',
                         f'Failed to export "{self.name}": {str(e)}')


class ProductProduct(models.Model):
    _inherit = 'product.product'

    woo_id = fields.Char(string='WooCommerce Variant ID', copy=False, index=True)


class WooBackendProduct(models.Model):
    _inherit = 'woo.backend'

    def _do_export_products(self, product_ids=None):
        domain = [('woo_backend_id', '=', self.id)]
        if product_ids:
            domain.append(('id', 'in', product_ids))
        products = self.env['product.template'].search(domain)
        exported = 0
        for product in products:
            try:
                product._export_to_woocommerce(self)
                exported += 1
            except Exception as e:
                self._log('export_products', 'error', str(e))
        self._log('export_products', 'success', f'Exported {exported} products.')
        return exported

    def _do_sync_stock(self):
        products = self.env['product.template'].search([
            ('woo_backend_id', '=', self.id),
            ('woo_id', '!=', False),
        ])
        synced = 0
        for product in products:
            try:
                self._api_put(f'products/{product.woo_id}', {
                    'stock_quantity': int(product.qty_available),
                    'manage_stock': True,
                })
                synced += 1
            except Exception as e:
                self._log('sync_stock', 'error',
                          f'Stock sync failed for "{product.name}": {str(e)}')
        self.last_stock_sync = fields.Datetime.now()
        self._log('sync_stock', 'success', f'Synced stock for {synced} products.')
        return synced
