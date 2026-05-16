import requests
from requests.auth import HTTPBasicAuth

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class WooBackend(models.Model):
    _name = 'woo.backend'
    _description = 'WooCommerce Backend'
    _rec_name = 'name'

    name = fields.Char(string='Store Name', required=True)
    active = fields.Boolean(default=True)
    store_url = fields.Char(
        string='Store URL',
        required=True,
        help='e.g. https://mystore.com',
    )
    consumer_key = fields.Char(string='Consumer Key', required=True)
    consumer_secret = fields.Char(string='Consumer Secret', required=True)
    version = fields.Selection([
        ('wc/v3', 'WooCommerce v3 (Recommended)'),
        ('wc/v2', 'WooCommerce v2'),
    ], string='API Version', default='wc/v3', required=True)

    # Sync Settings
    default_warehouse_id = fields.Many2one(
        'stock.warehouse', string='Default Warehouse',
    )
    default_pricelist_id = fields.Many2one(
        'product.pricelist', string='Default Pricelist',
    )
    default_lang_id = fields.Many2one(
        'res.lang', string='Default Language',
    )
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
    )

    # Auto sync
    auto_import_orders = fields.Boolean(string='Auto Import Orders', default=True)
    auto_sync_stock = fields.Boolean(string='Auto Sync Stock', default=True)
    order_status_filter = fields.Selection([
        ('any', 'Any Status'),
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
    ], string='Import Orders With Status', default='processing')

    # Stats
    last_order_import = fields.Datetime(string='Last Order Import', readonly=True)
    last_stock_sync = fields.Datetime(string='Last Stock Sync', readonly=True)

    # Counters
    product_count = fields.Integer(compute='_compute_counts')
    order_count = fields.Integer(compute='_compute_counts')
    log_count = fields.Integer(compute='_compute_counts')

    def _compute_counts(self):
        for rec in self:
            rec.product_count = self.env['product.template'].search_count(
                [('woo_backend_id', '=', rec.id)]
            )
            rec.order_count = self.env['sale.order'].search_count(
                [('woo_backend_id', '=', rec.id)]
            )
            rec.log_count = self.env['woo.log'].search_count(
                [('backend_id', '=', rec.id)]
            )

    def _get_api_url(self, endpoint):
        url = self.store_url.rstrip('/')
        return f'{url}/wp-json/{self.version}/{endpoint}'

    def _get_auth(self):
        return HTTPBasicAuth(self.consumer_key, self.consumer_secret)

    def _api_get(self, endpoint, params=None):
        url = self._get_api_url(endpoint)
        try:
            response = requests.get(
                url, auth=self._get_auth(),
                params=params or {}, timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ConnectionError:
            raise UserError(_('Cannot connect to WooCommerce store. Check the URL.'))
        except requests.exceptions.HTTPError as e:
            raise UserError(_('WooCommerce API Error: %s', str(e)))

    def _api_post(self, endpoint, data):
        url = self._get_api_url(endpoint)
        try:
            response = requests.post(
                url, auth=self._get_auth(),
                json=data, timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise UserError(_('WooCommerce API Error: %s', str(e)))

    def _api_put(self, endpoint, data):
        url = self._get_api_url(endpoint)
        try:
            response = requests.put(
                url, auth=self._get_auth(),
                json=data, timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise UserError(_('WooCommerce API Error: %s', str(e)))

    def _log(self, operation, status, message, record_id=None):
        self.env['woo.log'].create({
            'backend_id': self.id,
            'operation': operation,
            'status': status,
            'message': message,
            'woo_id': str(record_id) if record_id else False,
        })

    def action_test_connection(self):
        try:
            result = self._api_get('system_status')
            if result:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Connection Successful'),
                        'message': _('Connected to WooCommerce store successfully!'),
                        'type': 'success',
                    },
                }
        except Exception as e:
            raise UserError(_('Connection failed: %s', str(e)))

    def action_import_orders_now(self):
        self.ensure_one()
        try:
            count = self._do_import_orders()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Import Complete'),
                    'message': _(f'Successfully imported {count} orders!'),
                    'type': 'success',
                    'sticky': False,
                },
            }
        except Exception as e:
            raise UserError(str(e))

    def action_test_import_one(self):
        """Test import of first product only — shows exact error."""
        self.ensure_one()
        try:
            products = self._api_get('products', {'per_page': 1})
            if not products:
                raise UserError(_('No products found in WooCommerce!'))
            woo_product = products[0]
            woo_product['_variations'] = []
            result = self._import_basic_product(woo_product)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Test Import Success'),
                    'message': _(f'Product "{woo_product["name"]}" {result} successfully!'),
                    'type': 'success',
                    'sticky': True,
                },
            }
        except Exception as e:
            import traceback
            raise UserError(_(f'Import Error:\n{str(e)}\n\n{traceback.format_exc()}'))

    def action_test_api(self):
        """Test API and show what WooCommerce returns."""
        self.ensure_one()
        try:
            products = self._api_get('products', {'per_page': 5})
            orders = self._api_get('orders', {'per_page': 5})
            msg = (
                f'Products found: {len(products)}\n'
                f'Orders found: {len(orders)}\n'
                f'First product: {products[0].get("name") if products else "none"}'
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('API Test Result'),
                    'message': msg,
                    'type': 'success',
                    'sticky': True,
                },
            }
        except Exception as e:
            raise UserError(_(f'API Error: {str(e)}'))

    def action_import_products_now(self):
        self.ensure_one()
        try:
            count = self._do_import_products()
        except Exception as e:
            import traceback
            raise UserError(_(f'Import failed:\n{str(e)}\n\n{traceback.format_exc()}'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import Complete'),
                'message': _(f'Imported/updated {count} products! Check Sync Logs for details.'),
                'type': 'success' if count > 0 else 'warning',
                'sticky': True,
            },
        }

    def action_import_customers_now(self):
        self.ensure_one()
        try:
            count = self._do_import_customers()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Import Complete'),
                    'message': _(f'Successfully imported {count} customers!'),
                    'type': 'success',
                    'sticky': False,
                },
            }
        except Exception as e:
            raise UserError(str(e))

    def action_import_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Import Orders'),
            'res_model': 'woo.sync.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_backend_id': self.id,
                'default_operation': 'import_orders',
            },
        }

    def action_export_products(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Export Products'),
            'res_model': 'woo.sync.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_backend_id': self.id,
                'default_operation': 'export_products',
            },
        }

    def action_sync_stock(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sync Stock'),
            'res_model': 'woo.sync.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_backend_id': self.id,
                'default_operation': 'sync_stock',
            },
        }

    def action_view_products(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('WooCommerce Products'),
            'res_model': 'product.template',
            'view_mode': 'list,form',
            'domain': [('woo_backend_id', '=', self.id)],
        }

    def action_view_orders(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('WooCommerce Orders'),
            'res_model': 'sale.order',
            'view_mode': 'list,form',
            'domain': [('woo_backend_id', '=', self.id)],
        }

    def action_view_logs(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sync Logs'),
            'res_model': 'woo.log',
            'view_mode': 'list,form',
            'domain': [('backend_id', '=', self.id)],
        }

    @api.model
    def _cron_import_orders(self):
        for backend in self.search([('active', '=', True), ('auto_import_orders', '=', True)]):
            try:
                backend._do_import_orders()
            except Exception as e:
                backend._log('import_orders', 'error', str(e))

    @api.model
    def _cron_sync_stock(self):
        for backend in self.search([('active', '=', True), ('auto_sync_stock', '=', True)]):
            try:
                backend._do_sync_stock()
            except Exception as e:
                backend._log('sync_stock', 'error', str(e))
