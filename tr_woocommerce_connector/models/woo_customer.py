from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    woo_id = fields.Char(string='WooCommerce Customer ID', copy=False, index=True)
    woo_backend_id = fields.Many2one('woo.backend', string='WooCommerce Store', copy=False)


class WooBackendCustomer(models.Model):
    _inherit = 'woo.backend'

    def _get_or_create_partner(self, woo_customer):
        Partner = self.env['res.partner']
        email = woo_customer.get('email', '')
        woo_id = str(woo_customer.get('id', ''))

        partner = Partner.search([('woo_id', '=', woo_id)], limit=1)
        if partner:
            return partner

        if email:
            partner = Partner.search([('email', '=', email)], limit=1)

        billing = woo_customer.get('billing', {})
        vals = {
            'name': (billing.get('first_name', '') + ' ' + billing.get('last_name', '')).strip() or email or 'WooCommerce Customer',
            'email': email or billing.get('email', ''),
            'phone': billing.get('phone', ''),
            'street': billing.get('address_1', ''),
            'street2': billing.get('address_2', ''),
            'city': billing.get('city', ''),
            'zip': billing.get('postcode', ''),
            'woo_id': woo_id,
            'woo_backend_id': self.id,
        }

        country_code = billing.get('country', '')
        if country_code:
            country = self.env['res.country'].search([('code', '=', country_code)], limit=1)
            if country:
                vals['country_id'] = country.id

        if partner:
            partner.write({'woo_id': woo_id, 'woo_backend_id': self.id})
        else:
            partner = Partner.create(vals)

        return partner

    def _do_import_customers(self):
        params = {'per_page': 100, 'page': 1}
        imported = 0
        while True:
            customers = self._api_get('customers', params)
            if not customers:
                break
            for customer in customers:
                try:
                    self._get_or_create_partner(customer)
                    imported += 1
                except Exception as e:
                    self._log('import_customers', 'error', str(e))
            if len(customers) < 100:
                break
            params['page'] += 1
        self._log('import_customers', 'success', f'Imported {imported} customers.')
        return imported
