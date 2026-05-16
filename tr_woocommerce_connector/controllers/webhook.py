import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class WooCommerceWebhook(http.Controller):

    @http.route('/woocommerce/webhook/<int:backend_id>', type='http',
                auth='none', methods=['POST'], csrf=False)
    def woo_webhook(self, backend_id, **kwargs):
        try:
            body = request.httprequest.get_data(as_text=True)
            data = json.loads(body)

            topic = request.httprequest.headers.get('X-WC-Webhook-Topic', '')
            _logger.info('WooCommerce Webhook received: %s for backend %s', topic, backend_id)

            env = request.env(user=request.env.ref('base.user_admin').id)
            backend = env['woo.backend'].sudo().browse(backend_id)

            if not backend.exists():
                return request.make_response('Backend not found', status=404)

            if topic == 'order.updated' or topic == 'order.created':
                backend.sudo()._process_webhook_order(data)
            elif topic == 'order.deleted':
                backend.sudo()._process_webhook_order_deleted(data)
            elif topic == 'product.updated' or topic == 'product.created':
                backend.sudo()._import_single_product(data)

            return request.make_response('OK', status=200)

        except Exception as e:
            _logger.error('WooCommerce Webhook error: %s', str(e))
            return request.make_response(str(e), status=500)
