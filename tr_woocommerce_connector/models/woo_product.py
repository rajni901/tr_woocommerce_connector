import base64
import requests as req

from odoo import _, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    woo_id = fields.Char(string='WooCommerce ID', copy=False, index=True)
    woo_backend_id = fields.Many2one('woo.backend', string='WooCommerce Store', copy=False)
    woo_last_sync = fields.Datetime(string='Last WooCommerce Sync', readonly=True)
    woo_type = fields.Char(string='WooCommerce Type', readonly=True)

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

    def _download_image(self, url):
        try:
            response = req.get(url, timeout=15)
            if response.status_code == 200:
                return base64.b64encode(response.content)
        except Exception:
            pass
        return False

    def _get_or_create_attribute(self, name, values):
        Attribute = self.env['product.attribute']
        AttributeValue = self.env['product.attribute.value']

        attr = Attribute.search([('name', '=ilike', name)], limit=1)
        if not attr:
            attr = Attribute.create({'name': name, 'create_variant': 'always'})

        attr_values = []
        for val in values:
            av = AttributeValue.search([
                ('attribute_id', '=', attr.id),
                ('name', '=ilike', val),
            ], limit=1)
            if not av:
                av = AttributeValue.create({
                    'attribute_id': attr.id,
                    'name': val,
                })
            attr_values.append(av.id)

        return attr, attr_values

    def _do_import_products(self):
        params = {'per_page': 100, 'page': 1}
        imported, updated = 0, 0

        while True:
            products = self._api_get('products', params)
            if not products:
                break

            for woo_product in products:
                try:
                    result = self._import_single_product(woo_product)
                    if result == 'created':
                        imported += 1
                    elif result == 'updated':
                        updated += 1
                except Exception as e:
                    self._log('import_products', 'error',
                              f'Product "{woo_product.get("name")}": {str(e)}')

            if len(products) < 100:
                break
            params['page'] += 1

        self._log('import_products', 'success',
                  f'Imported {imported} new, updated {updated} products.')
        return imported + updated

    def _import_single_product(self, woo_product):
        woo_id = str(woo_product['id'])
        sku = woo_product.get('sku', '')
        woo_type = woo_product.get('type', 'simple')

        existing = self.env['product.template'].search(
            [('woo_id', '=', woo_id)], limit=1
        )
        if not existing and sku:
            existing = self.env['product.template'].search(
                [('default_code', '=', sku)], limit=1
            )

        vals = {
            'name': woo_product.get('name', 'WooCommerce Product'),
            'list_price': float(woo_product.get('price') or
                                woo_product.get('regular_price') or 0),
            'description_sale': woo_product.get('short_description', ''),
            'description': woo_product.get('description', ''),
            'woo_id': woo_id,
            'woo_backend_id': self.id,
            'woo_last_sync': fields.Datetime.now(),
            'woo_type': woo_type,
        }

        if sku:
            vals['default_code'] = sku

        # Download main image
        images = woo_product.get('images', [])
        if images and images[0].get('src'):
            img = self._download_image(images[0]['src'])
            if img:
                vals['image_1920'] = img

        # Handle attributes for variable products
        attributes = woo_product.get('attributes', [])
        attribute_line_ids = []
        if attributes and woo_type == 'variable':
            for attr_data in attributes:
                if not attr_data.get('variation'):
                    continue
                attr_name = attr_data.get('name', '')
                attr_options = attr_data.get('options', [])
                if not attr_name or not attr_options:
                    continue
                attr, attr_value_ids = self._get_or_create_attribute(
                    attr_name, attr_options
                )
                attribute_line_ids.append((0, 0, {
                    'attribute_id': attr.id,
                    'value_ids': [(6, 0, attr_value_ids)],
                }))

        if existing:
            existing.write(vals)
            if attribute_line_ids:
                existing.attribute_line_ids.unlink()
                existing.write({'attribute_line_ids': attribute_line_ids})
            # Import variations
            if woo_type == 'variable':
                self._import_variations(existing, woo_id)
            return 'updated'
        else:
            if attribute_line_ids:
                vals['attribute_line_ids'] = attribute_line_ids
            product = self.env['product.template'].create(vals)
            if woo_type == 'variable':
                self._import_variations(product, woo_id)
            return 'created'

    def _import_variations(self, product_tmpl, woo_product_id):
        variations = self._api_get(f'products/{woo_product_id}/variations',
                                   {'per_page': 100})
        if not variations:
            return

        for var in variations:
            var_id = str(var['id'])
            sku = var.get('sku', '')
            price = float(var.get('price') or var.get('regular_price') or 0)

            # Match variant by attribute values
            var_attrs = {
                a['name']: a['option']
                for a in var.get('attributes', [])
            }

            # Find matching Odoo variant
            variant = self.env['product.product'].search(
                [('woo_id', '=', var_id)], limit=1
            )
            if not variant and sku:
                variant = self.env['product.product'].search(
                    [('default_code', '=', sku),
                     ('product_tmpl_id', '=', product_tmpl.id)], limit=1
                )

            # Try to match by attribute combination
            if not variant:
                for v in product_tmpl.product_variant_ids:
                    v_attrs = {
                        ptav.attribute_id.name: ptav.name
                        for ptav in v.product_template_attribute_value_ids
                    }
                    if v_attrs == var_attrs:
                        variant = v
                        break

            if variant:
                variant_vals = {'woo_id': var_id}
                if sku:
                    variant_vals['default_code'] = sku
                if price:
                    variant_vals['lst_price'] = price
                variant.write(variant_vals)

            # Download variant image
            images = var.get('image', {})
            if images and images.get('src') and variant:
                img = self._download_image(images['src'])
                if img:
                    variant.write({'image_1920': img})

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
