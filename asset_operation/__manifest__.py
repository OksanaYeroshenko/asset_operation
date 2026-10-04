{
    'name': 'Asset Operations',
    'summary': 'Assign assets to employees and take them back, keep the history, print and sign the acts',
    'author': 'Oksana Yeroshenko',
    'website': 'https://odoo-pro.com.ua',
    'category': 'Human Resources',
    'version': '18.0.0.1.0',
    'depends': ['hr_maintenance', 'account_asset', 'sign'],
    'data': [
        'security/asset_operation_security.xml',
        'security/ir.model.access.csv',
        'report/asset_operation_report.xml',
        'wizard/asset_operation_wizard_views.xml',
        'views/maintenance_equipment_views.xml',
        'views/asset_operation_views.xml',
    ],
    'demo': [
        'demo/asset_operation_demo.xml',
    ],
    'license': 'LGPL-3',
}
