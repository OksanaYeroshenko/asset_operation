from odoo import models


class SignSendRequest(models.TransientModel):
    _inherit = 'sign.send.request'

    def create_request(self):
        """Link the request to the operation whose act is being sent, so its state and document show there."""
        request = super().create_request()
        if self.env.context.get('asset_operation_id'):
            self.env['asset.operation'].browse(self.env.context['asset_operation_id']).sign_request_id = request
        return request
