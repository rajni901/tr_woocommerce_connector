from odoo import _, api, fields, models

# WooCommerce → Odoo status mapping
WOO_STATUS_MAP = {
    'pending': 'draft',
    'processing': 'sale',
    'on-hold': 'draft',
    'completed': 'done',
    'cancelled': 'cancel',
    'refunded': 'cancel',
    'failed': 'cancel',
}


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    woo_id = fields.Char(string='WooCommerce Order ID', copy=False, index=True)
    woo_backend_id = fields.Many2one('woo.backend', string='WooCommerce Store', copy=False)
    woo_order_status = fields.Char(string='WooCommerce Status', readonly=True)
    woo_order_number = fields.Char(string='WooCommerce Order #', readonly=True)


class WooBackendOrder(models.Model):
    _inherit = 'woo.backend'

    def _process_webhook_order(self, data):
        """Process incoming order webhook from WooCommerce."""
        woo_id = str(data.get('id', ''))
        if not woo_id:
            return

        existing = self.env['sale.order'].search(
            [('woo_id', '=', woo_id)], limit=1
        )
        woo_status = data.get('status', '')

        if existing:
            # Update status
            existing.write({'woo_order_status': woo_status})
            self._apply_woo_status(existing, woo_status)
            self._log('import_orders', 'success',
                      f'Order #{data.get("number")} status updated to "{woo_status}"', woo_id)
        else:
            # New order — import it
            self._create_or_update_order(data)

    def _process_webhook_order_deleted(self, data):
        woo_id = str(data.get('id', ''))
        order = self.env['sale.order'].search([('woo_id', '=', woo_id)], limit=1)
        if order and order.state == 'draft':
            order.action_cancel()

    def _apply_woo_status(self, order, woo_status):
        """Apply WooCommerce status to Odoo sale order."""
        if woo_status in ('processing', 'completed') and order.state == 'draft':
            try:
                order.action_confirm()
            except Exception:
                pass
        elif woo_status == 'cancelled' and order.state in ('draft', 'sent'):
            try:
                order.action_cancel()
            except Exception:
                pass

    def _do_sync_order_statuses(self):
        """Poll WooCommerce for recently updated orders and sync status."""
        import datetime
        since = self.last_order_import or (
            fields.Datetime.now() - datetime.timedelta(hours=24)
        )
        params = {
            'per_page': 100,
            'modified_after': since.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        orders = self._api_get('orders', params)
        updated = 0
        for woo_order in (orders or []):
            woo_id = str(woo_order['id'])
            existing = self.env['sale.order'].search(
                [('woo_id', '=', woo_id)], limit=1
            )
            if existing:
                woo_status = woo_order.get('status', '')
                existing.write({'woo_order_status': woo_status})
                self._apply_woo_status(existing, woo_status)
                updated += 1
        self._log('import_orders', 'success', f'Synced status for {updated} orders.')
        return updated

    def action_register_webhooks(self):
        """Register webhooks in WooCommerce to receive real-time updates."""
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        webhook_url = f'{base_url}/woocommerce/webhook/{self.id}'

        topics = [
            ('order.created', 'Odoo: Order Created'),
            ('order.updated', 'Odoo: Order Updated'),
            ('order.deleted', 'Odoo: Order Deleted'),
        ]
        registered = 0
        for topic, name in topics:
            try:
                self._api_post('webhooks', {
                    'name': name,
                    'topic': topic,
                    'delivery_url': webhook_url,
                    'status': 'active',
                })
                registered += 1
            except Exception as e:
                self._log('import_orders', 'warning',
                          f'Webhook "{topic}" registration failed: {str(e)}')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Webhooks Registered'),
                'message': _(f'Registered {registered} webhooks. URL: {webhook_url}'),
                'type': 'success',
                'sticky': True,
            },
        }

    def _do_import_orders(self, date_from=None):
        params = {'per_page': 100, 'page': 1}
        if self.order_status_filter != 'any':
            params['status'] = self.order_status_filter
        if date_from:
            params['after'] = date_from.strftime('%Y-%m-%dT%H:%M:%S')
        elif self.last_order_import:
            params['after'] = self.last_order_import.strftime('%Y-%m-%dT%H:%M:%S')

        # Fetch all orders first
        all_orders = []
        while True:
            orders = self._api_get('orders', params)
            if not orders:
                break
            all_orders.extend(orders)
            if len(orders) < 100:
                break
            params['page'] += 1

        # Process with DB operations
        imported = 0
        for woo_order in all_orders:
            try:
                with self.env.cr.savepoint():
                    self._create_or_update_order(woo_order)
                    imported += 1
            except Exception as e:
                self._log('import_orders', 'error',
                          f'Order #{woo_order.get("number")}: {str(e)}',
                          woo_order.get('id'))

        self.last_order_import = fields.Datetime.now()
        self._log('import_orders', 'success', f'Imported {imported} orders.')
        return imported

    def _create_or_update_order(self, woo_order):
        SaleOrder = self.env['sale.order']
        woo_id = str(woo_order['id'])

        existing = SaleOrder.search([('woo_id', '=', woo_id)], limit=1)
        if existing:
            existing.write({'woo_order_status': woo_order.get('status', '')})
            return existing

        partner = self._get_or_create_partner_from_order(woo_order)
        warehouse = self.default_warehouse_id or self.env['stock.warehouse'].search(
            [('company_id', '=', self.company_id.id)], limit=1
        )

        order_vals = {
            'partner_id': partner.id,
            'woo_id': woo_id,
            'woo_backend_id': self.id,
            'woo_order_status': woo_order.get('status', ''),
            'woo_order_number': str(woo_order.get('number', '')),
            'client_order_ref': f"WOO-{woo_order.get('number', '')}",
            'company_id': self.company_id.id,
            'warehouse_id': warehouse.id if warehouse else False,
        }

        if self.default_pricelist_id:
            order_vals['pricelist_id'] = self.default_pricelist_id.id

        order = SaleOrder.create(order_vals)
        self._create_order_lines(order, woo_order.get('line_items', []))
        return order

    def _get_or_create_partner_from_order(self, woo_order):
        billing = woo_order.get('billing', {})
        woo_customer_id = str(woo_order.get('customer_id', 0))

        if woo_customer_id and woo_customer_id != '0':
            partner = self.env['res.partner'].search(
                [('woo_id', '=', woo_customer_id)], limit=1
            )
            if partner:
                return partner

        email = billing.get('email', '')
        if email:
            partner = self.env['res.partner'].search(
                [('email', '=', email)], limit=1
            )
            if partner:
                return partner

        name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
        vals = {
            'name': name or 'WooCommerce Customer',
            'email': email,
            'phone': billing.get('phone', ''),
            'street': billing.get('address_1', ''),
            'street2': billing.get('address_2', ''),
            'city': billing.get('city', ''),
            'zip': billing.get('postcode', ''),
            'woo_id': woo_customer_id if woo_customer_id != '0' else False,
            'woo_backend_id': self.id,
        }
        country_code = billing.get('country', '')
        if country_code:
            country = self.env['res.country'].search([('code', '=', country_code)], limit=1)
            if country:
                vals['country_id'] = country.id

        return self.env['res.partner'].create(vals)

    def _create_order_lines(self, order, line_items):
        SaleOrderLine = self.env['sale.order.line']
        for item in line_items:
            product = self._find_or_create_product(item)
            line_vals = {
                'order_id': order.id,
                'product_id': product.id,
                'name': item.get('name', product.name),
                'product_uom_qty': float(item.get('quantity', 1)),
                'price_unit': float(item.get('price', 0)),
            }
            SaleOrderLine.create(line_vals)

    def _find_or_create_product(self, item):
        woo_product_id = str(item.get('product_id', ''))
        sku = item.get('sku', '')

        product_tmpl = False
        if woo_product_id:
            product_tmpl = self.env['product.template'].search(
                [('woo_id', '=', woo_product_id)], limit=1
            )
        if not product_tmpl and sku:
            product_tmpl = self.env['product.template'].search(
                [('default_code', '=', sku)], limit=1
            )
        if not product_tmpl:
            product_tmpl = self.env['product.template'].create({
                'name': item.get('name', 'WooCommerce Product'),
                'default_code': sku or '',
                'list_price': float(item.get('price', 0)),
                'woo_id': woo_product_id,
                'woo_backend_id': self.id,
                'type': 'consu',
            })

        return product_tmpl.product_variant_id
