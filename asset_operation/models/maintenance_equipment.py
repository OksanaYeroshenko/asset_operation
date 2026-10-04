from odoo import models, fields


class MaintenanceEquipment(models.Model):
    _inherit = 'maintenance.equipment'

    account_asset_id = fields.Many2one(
        'account.asset', string='Accounting Asset', check_company=True,
        domain="[('state', 'not in', ('model', 'cancelled')), ('parent_id', '=', False)]")
    currency_id = fields.Many2one(related='account_asset_id.currency_id')
    book_value = fields.Monetary(related='account_asset_id.book_value')
    operation_ids = fields.One2many(
        'asset.operation', 'equipment_id', string='Operations', groups='asset_operation.group_asset_custodian')
