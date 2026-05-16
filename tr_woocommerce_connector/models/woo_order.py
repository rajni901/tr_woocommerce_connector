from odoo import _, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    woo_id = fields.Char(string='WooCommerce Order ID', copy=False, index=True)
    woo_backend_id = fields.Many2one('woo.backend', string='WooCommerce Store', copy=False)
    woo_order_status = fields.Char(string='WooCommerce Status', readonly=True)
    woo_order_number = fields.Char(string='WooCommerce Order #', readonly=True)


class WooBackendOrder(models.Model):
    _inherit = 'woo.backend'

    def _do_import_orders(self, date_from=None):
        params = {'per_page': 100, 'page': 1}
        if self.order_status_filter != 'any':
            params['status'] = self.order_status_filter
        if date_from:
            params['after'] = date_from.strftime('%Y-%m-%dT%H:%M:%S')
        elif self.last_order_import:
            params['after'] = self.last_order_import.strftime('%Y-%m-%dT%H:%M:%S')

        imported = 0
        while True:
            orders = self._api_get('orders', params)
            if not orders:
                break
            for woo_order in orders:
                try:
                    self._create_or_update_order(woo_order)
                    imported += 1
                except Exception as e:
                    self._log('import_orders', 'error',
                              f'Order #{woo_order.get("number")}: {str(e)}',
                              woo_order.get('id'))
            if len(orders) < 100:
                break
            params['page'] += 1

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
